"""XBRL financial-facts extraction with citations.

Lessons baked in from probing EDGAR's company-facts (see commit notes):
  * `fiscal_year` on a fact is unreliable — partial-period contexts leak in. We key off the
    real period boundaries (`period_start`/`period_end`) and the period length instead.
  * The same us-gaap concept appears in multiple contexts (balance sheet line vs. footnote
    table vs. dimensioned breakdowns) with different values. Filtering by `statement_type`
    and excluding dimensioned facts isolates the canonical statement value.
  * Tags vary by issuer (AAL books capex as `PaymentsToAcquireProductiveAssets`, total debt as
    `LongTermDebt`), so every metric carries a priority list of candidate concepts.

The output is a `FinancialSeries`: one `YearFacts` per fiscal year, each metric pointing at the
exact `Fact` it came from, so the API can render a citation for every number.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace
from typing import Optional

from ..schemas import Citation, CitedValue
from .client import index_url_for

# ---------------------------------------------------------------------------
# Metric specs: key -> (statement_type, kind, candidate concepts in priority order)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    statement: Optional[str]   # 'BalanceSheet' | 'IncomeStatement' | 'CashFlowStatement' | None
    kind: str                  # 'instant' | 'duration'
    concepts: tuple[str, ...]
    # A charge added back to EBITDA. Its concepts nest (a total contains its parts), so the
    # largest tagged value per period wins, and an untagged period is no charge, not unknown.
    addback: bool = False


METRIC_SPECS: tuple[MetricSpec, ...] = (
    # --- balance sheet (instant) ---
    # LongTermDebt is defined to include current maturities, but issuers tag their
    # noncurrent line with it (AAL), so it is read as the noncurrent fallback.
    MetricSpec("lt_debt_noncurrent", "Long-term debt (noncurrent)", "BalanceSheet", "instant",
               ("LongTermDebtNoncurrent", "LongTermDebt",
                "LongTermDebtAndCapitalLeaseObligations")),
    MetricSpec("lt_debt_current", "Current maturities of long-term debt", "BalanceSheet", "instant",
               ("LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent",
                "DebtCurrent")),
    MetricSpec("short_term_debt", "Short-term borrowings", "BalanceSheet", "instant",
               ("ShortTermBorrowings", "CommercialPaper")),
    # in Chapter 11 the pre-petition debt moves off the debt lines into this one (TSE Q2
    # 2026: $2,562M subject to compromise beside $592M of debt); the total also carries
    # other pre-petition claims, so the debt-only concept is preferred when tagged
    MetricSpec("debt_subject_to_compromise", "Liabilities subject to compromise",
               "BalanceSheet", "instant",
               ("LiabilitiesSubjectToCompromiseDebtAndAccruedInterest",
                "LiabilitiesSubjectToCompromise")),
    # balance-sheet cash before the cash-flow total that includes restricted cash (AAL tags
    # only Cash and that total); its short-term investments line is AFS debt securities
    MetricSpec("cash", "Cash & equivalents", "BalanceSheet", "instant",
               ("CashAndCashEquivalentsAtCarryingValue",
                "CashAndCashEquivalentsAtCarryingValueIncludingDiscontinuedOperations",
                "Cash", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents")),
    MetricSpec("short_term_investments", "Short-term investments", "BalanceSheet", "instant",
               ("ShortTermInvestments", "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
                "MarketableSecuritiesCurrent")),
    MetricSpec("restricted_cash", "Restricted cash", "BalanceSheet", "instant",
               ("RestrictedCashAndCashEquivalents", "RestrictedCash",
                "RestrictedCashAndCashEquivalentsNoncurrent", "RestrictedCashNoncurrent",
                "RestrictedCashCurrent")),
    MetricSpec("accounts_payable", "Accounts payable", "BalanceSheet", "instant",
               ("AccountsPayableCurrent", "AccountsPayableTradeCurrent",
                "AccountsPayableAndAccruedLiabilitiesCurrent")),
    MetricSpec("inventory", "Inventory", "BalanceSheet", "instant",
               ("InventoryNet", "AirlineRelatedInventoryNet", "InventoryFinishedGoodsNetOfReserves")),
    # --- liquidation-analysis assets (Moyer asset-based valuation) ---
    MetricSpec("accounts_receivable", "Accounts receivable (net)", "BalanceSheet", "instant",
               ("ReceivablesNetCurrent", "AccountsReceivableNetCurrent", "AccountsReceivableNet")),
    MetricSpec("ppe_net", "PP&E (net)", "BalanceSheet", "instant",
               ("PropertyPlantAndEquipmentNet",
                "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization")),
    MetricSpec("goodwill", "Goodwill", "BalanceSheet", "instant", ("Goodwill",)),
    MetricSpec("intangibles", "Intangible assets (ex-goodwill)", "BalanceSheet", "instant",
               ("IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet")),
    MetricSpec("op_lease_noncurrent", "Operating lease liability (noncurrent)", "BalanceSheet",
               "instant", ("OperatingLeaseLiabilityNoncurrent",)),
    MetricSpec("op_lease_current", "Operating lease liability (current)", "BalanceSheet", "instant",
               ("OperatingLeaseLiabilityCurrent",)),
    MetricSpec("fin_lease_noncurrent", "Finance lease liability (noncurrent)", "BalanceSheet",
               "instant", ("FinanceLeaseLiabilityNoncurrent",)),
    MetricSpec("fin_lease_current", "Finance lease liability (current)", "BalanceSheet", "instant",
               ("FinanceLeaseLiabilityCurrent",)),
    MetricSpec("pension_benefit_obligation", "Pension/OPEB benefit obligation", "BalanceSheet",
               "instant", ("DefinedBenefitPlanFundedStatusOfPlanAmount",
                           "DefinedBenefitPlanBenefitObligation")),
    # --- credit-scoring metrics (merged in from hazard's facts.py) ---
    MetricSpec("total_assets", "Total assets", "BalanceSheet", "instant", ("Assets",)),
    MetricSpec("total_liabilities", "Total liabilities", "BalanceSheet", "instant",
               ("Liabilities",)),
    MetricSpec("current_assets", "Current assets", "BalanceSheet", "instant", ("AssetsCurrent",)),
    MetricSpec("current_liabilities", "Current liabilities", "BalanceSheet", "instant",
               ("LiabilitiesCurrent",)),
    MetricSpec("retained_earnings", "Retained earnings", "BalanceSheet", "instant",
               ("RetainedEarningsAccumulatedDeficit",)),
    MetricSpec("stockholders_equity", "Stockholders' equity", "BalanceSheet", "instant",
               ("StockholdersEquity",
                "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest")),
    # mezzanine (temporary equity) — the recast-as-debt input (Moyer ch. 6); the
    # including-NCI variant may carry redeemable NCI (caveated where displayed)
    MetricSpec("temporary_equity", "Temporary equity (mezzanine)", "BalanceSheet", "instant",
               ("TemporaryEquityCarryingAmountAttributableToParent",
                "TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests")),
    # per-quarter Merton E = shares × price. us-gaap concept lands exactly on the balance-sheet
    # date; the dei cover-page tag is dated days later so it rarely anchors — kept as fallback.
    MetricSpec("shares_outstanding", "Common shares outstanding", "BalanceSheet", "instant",
               ("CommonStockSharesOutstanding", "dei:EntityCommonStockSharesOutstanding")),
    # --- income statement (duration) ---
    MetricSpec("revenue", "Revenue", "IncomeStatement", "duration",
               ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet")),
    MetricSpec("cogs", "Cost of goods/services sold", "IncomeStatement", "duration",
               ("CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold")),
    MetricSpec("operating_expenses", "Total operating expenses", "IncomeStatement", "duration",
               ("CostsAndExpenses", "OperatingExpenses", "BenefitsLossesAndExpenses",
                "OperatingCostsAndExpenses")),
    MetricSpec("operating_income", "Operating income", "IncomeStatement", "duration",
               ("OperatingIncomeLoss",)),
    MetricSpec("interest_expense", "Interest expense", "IncomeStatement", "duration",
               ("InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt",
                "InterestAndDebtExpense")),
    # rent for EBITDAR: usually disclosed in the lease footnote, so no statement filter
    MetricSpec("operating_lease_cost", "Operating lease cost (rent)", None, "duration",
               ("OperatingLeaseCost", "LeaseAndRentalExpense",
                "OperatingLeasesRentExpenseNet", "AircraftRental")),
    MetricSpec("net_income", "Net income", "IncomeStatement", "duration",
               ("NetIncomeLoss", "ProfitLoss")),
    MetricSpec("income_tax_expense", "Income tax expense", "IncomeStatement", "duration",
               ("IncomeTaxExpenseBenefit",)),
    MetricSpec("pretax_income", "Pre-tax income", "IncomeStatement", "duration",
               ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments")),
    # tax-footnote ratio (decimal, e.g. 0.24); no statement filter
    MetricSpec("effective_tax_rate", "Effective tax rate", None, "duration",
               ("EffectiveIncomeTaxRateContinuingOperations",)),
    # GROSS NOL carryforward (Moyer ch. 11 §382). Gross concept ONLY — do NOT add
    # DeferredTaxAssetsOperatingLossCarryforwards (that is the tax-effected DTA ≈ NOL×rate,
    # a different quantity); often tagged only dimensioned-by-jurisdiction (then skipped),
    # so manual entry is the expected fallback. Footnote item, no statement filter.
    MetricSpec("nol_carryforward", "NOL carryforward", None, "instant",
               ("OperatingLossCarryforwards",)),
    # --- cash flow (duration) ---
    MetricSpec("d_and_a", "Depreciation & amortization", "CashFlowStatement", "duration",
               ("DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
                "DepreciationAmortizationAndAccretionNet")),
    MetricSpec("operating_cash_flow", "Operating cash flow", "CashFlowStatement", "duration",
               ("NetCashProvidedByUsedInOperatingActivities",)),
    MetricSpec("capex", "Capital expenditures", "CashFlowStatement", "duration",
               ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
                "PaymentsForCapitalImprovements", "PaymentsToAcquireOtherProductiveAssets")),
    # --- RP-basket builder flows (Moyer ch. 7) — data-only, consumed by quarterly_flows;
    # three separate specs: a spec's concepts are alternates, never summed ---
    MetricSpec("equity_issuance_proceeds", "Proceeds from equity issuance", "CashFlowStatement",
               "duration",
               ("ProceedsFromIssuanceOfCommonStock", "ProceedsFromIssuanceOrSaleOfEquity",
                "ProceedsFromIssuanceOfPreferredStockAndPreferenceStock")),
    MetricSpec("dividends_paid", "Dividends paid", "CashFlowStatement", "duration",
               ("PaymentsOfDividends", "PaymentsOfDividendsCommonStock")),
    MetricSpec("stock_repurchases", "Stock repurchases", "CashFlowStatement", "duration",
               ("PaymentsForRepurchaseOfCommonStock", "PaymentsForRepurchaseOfEquity")),
    # --- common covenant EBITDA add-backs (quantified when tagged; no statement filter) ---
    MetricSpec("share_based_comp", "Stock-based compensation", None, "duration",
               ("ShareBasedCompensation", "AllocatedShareBasedCompensationExpense")),
    MetricSpec("restructuring", "Restructuring charges", None, "duration",
               ("RestructuringCharges", "RestructuringCostsAndAssetImpairmentCharges",
                "RestructuringSettlementAndImpairmentProvisions", "RestructuringCosts")),
    # ATUS: the FY2025 10-K tags its $1.6bn franchise-rights charge as indefinite-lived
    # intangible impairment; its 10-Qs use GoodwillAndIntangibleAssetImpairment.
    # ponytail: largest-concept rule undercounts two disjoint charges with no total tagged
    # (goodwill + tangible); sum the components if that shows up in a hero.
    MetricSpec("impairment", "Impairment charges", None, "duration",
               ("AssetImpairmentCharges", "GoodwillAndIntangibleAssetImpairment",
                "GoodwillImpairmentLoss", "ImpairmentOfIntangibleAssetsExcludingGoodwill",
                "ImpairmentOfIntangibleAssetsIndefinitelivedExcludingGoodwill",
                "ImpairmentOfLongLivedAssetsHeldForUse", "TangibleAssetImpairmentCharges"),
               addback=True),
)

_SPEC_BY_KEY = {s.key: s for s in METRIC_SPECS}
_ANCHOR_CONCEPTS = ("revenue", "operating_income", "operating_cash_flow")
_MIN_DAYS, _MAX_DAYS = 340, 380


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


@dataclass
class YearFacts:
    fiscal_year: int
    period_end: dt.date
    metrics: dict[str, object] = field(default_factory=dict)  # key -> raw edgartools Fact

    def get(self, key: str):
        return self.metrics.get(key)


@dataclass
class FinancialSeries:
    cik: str
    years: list[YearFacts]

    def latest(self) -> Optional[YearFacts]:
        return self.years[-1] if self.years else None

    def value(self, fiscal_year: int, key: str) -> Optional[float]:
        for y in self.years:
            if y.fiscal_year == fiscal_year:
                f = y.get(key)
                return getattr(f, "numeric_value", None) if f else None
        return None


def _is_instant(fact) -> bool:
    if getattr(fact, "period_type", None) == "instant":
        return True
    ps, pe = getattr(fact, "period_start", None), getattr(fact, "period_end", None)
    return pe is not None and (ps is None or ps == pe)


def _duration_days(fact) -> Optional[int]:
    ps, pe = getattr(fact, "period_start", None), getattr(fact, "period_end", None)
    if ps and pe:
        return (pe - ps).days
    return None


def _rank(fact, statement: Optional[str]) -> tuple:
    """Higher is better. Prefer 10-K, the matching statement, recent filings, high confidence."""
    is_10k = 1 if (getattr(fact, "form_type", "") or "").startswith("10-K") else 0
    stmt_match = 1 if (statement and getattr(fact, "statement_type", None) == statement) else 0
    filed = getattr(fact, "filing_date", None)
    filed_ord = filed.toordinal() if isinstance(filed, dt.date) else 0
    conf = getattr(fact, "confidence_score", 0) or 0
    return (stmt_match, is_10k, filed_ord, conf)


def _query_concept(entity_facts, concept: str, statement: Optional[str]):
    """Return raw facts for a concept, trying the statement filter first, then unfiltered.
    Bare concept names get the us-gaap prefix; qualified ones (e.g. dei:…) pass through.
    Exact match only: the default substring match lets 'LongTermDebt' pull in
    LongTermDebtCurrent and 'Liabilities' pull in LiabilitiesAndStockholdersEquity."""
    base = concept if ":" in concept else f"us-gaap:{concept}"
    results = []
    if statement:
        try:
            results = (entity_facts.query().by_concept(base, exact=True)
                       .by_statement_type(statement).execute())
        except Exception:
            results = []
    if not results:
        try:
            results = entity_facts.query().by_concept(base, exact=True).execute()
        except Exception:
            results = []
    return results


def _select_metric(entity_facts, spec: MetricSpec, anchors: dict[int, dt.date],
                   accession: Optional[str] = None) -> dict[int, object]:
    """For each anchor fiscal year, pick the best fact from the first concept present IN
    THAT YEAR — issuers migrate tags (AAL: InterestExpense through FY2023, then
    InterestExpenseNonoperating), so a concept that covers only early years must not block
    the fallbacks for later ones. An add-back takes its largest concept instead.
    A statement line is tagged in every 10-Q too, so for a statement flow a concept the issuer
    also reports for interim periods goes ahead of an annual-only one, and the fiscal years
    share the definition the TTM strip uses (AAL's D&A: DepreciationDepletionAndAmortization
    is an annual-only, rounded $2.2bn; the quarterly DepreciationAndAmortization is $1,890M).
    `accession` restricts the candidates to one filing."""
    out: dict[int, object] = {}
    by_concept = [_query_concept(entity_facts, c, spec.statement) for c in spec.concepts]
    if spec.kind == "duration" and spec.statement:
        by_concept.sort(key=lambda fs: not _has_interim(fs))
    for facts in by_concept:
        per_year: dict[int, list] = {}
        for f in facts:
            if getattr(f, "is_dimensioned", False):
                continue
            if getattr(f, "numeric_value", None) is None:
                continue
            pe = getattr(f, "period_end", None)
            if pe is None:
                continue
            if spec.kind == "instant":
                if not _is_instant(f):
                    continue
                year = next((y for y, d in anchors.items() if d == pe), None)
            else:
                d = _duration_days(f)
                if d is None or not (_MIN_DAYS <= d <= _MAX_DAYS):
                    continue
                year = pe.year if pe.year in anchors else None
            if year is None or (year in out and not spec.addback):
                continue
            if accession and getattr(f, "accession", None) != accession:
                continue
            per_year.setdefault(year, []).append(f)
        for y, cands in per_year.items():
            best = max(cands, key=lambda fc: _rank(fc, spec.statement))
            if y not in out or best.numeric_value > out[y].numeric_value:
                out[y] = best
    return out


# the components summed into total debt (capstack.forensic, hazard.features)
DEBT_KEYS = ("lt_debt_noncurrent", "lt_debt_current", "short_term_debt",
             "debt_subject_to_compromise")
_LTD = MetricSpec("long_term_debt", "Long-term debt", "BalanceSheet", "instant", ("LongTermDebt",))


def _ltd_is_total(entity_facts) -> bool:
    """Whether the issuer's LongTermDebt is its total, noncurrent + current, as the taxonomy
    defines it (ATUS Q2 2026: 26,550.8 = 22,402.9 + 4,147.8), rather than its noncurrent
    line (AAL). Read off the latest balance sheet tagging LongTermDebt and
    LongTermDebtCurrent: issuers change convention (AMR's 2012 10-K used the total)."""
    def by_filing(concept: str) -> dict:
        return {(f.accession, f.period_end): f.numeric_value
                for f in _query_concept(entity_facts, concept, "BalanceSheet")
                if not getattr(f, "is_dimensioned", False) and _is_instant(f)
                and getattr(f, "numeric_value", None) is not None}
    ltd, nc, cur = (by_filing(c) for c in ("LongTermDebt", "LongTermDebtNoncurrent",
                                           "LongTermDebtCurrent"))
    both = [k for k in ltd if k in cur]
    if not both:
        return False
    k = max(both, key=lambda k: k[1])
    return k in nc and abs(ltd[k] - nc[k] - cur[k]) <= 1e-3 * abs(ltd[k])


def _align_debt(entity_facts, metrics: dict, anchor: dict, ltd_is_total: bool) -> None:
    """The debt components are summed into total debt, so they must come from one balance
    sheet: the best-ranked filing that reports any of them. Issuers re-tag comparatives
    (LCID's FY2024 $126M is ShortTermBorrowings in that 10-K, LongTermDebtCurrent in the
    next), so a component taken from another filing is re-read from that one, or dropped
    when that balance sheet doesn't carry it. `anchor` is the one {key: date} entry these
    metrics belong to; `ltd_is_total` is _ltd_is_total for this issuer."""
    present = [metrics[k] for k in DEBT_KEYS if k in metrics]
    if not present:
        return
    acc = getattr(max(present, key=lambda f: _rank(f, "BalanceSheet")), "accession", None)
    for key in DEBT_KEYS:
        if key in metrics and getattr(metrics[key], "accession", None) != acc:
            del metrics[key]
            same_filing = _select_metric(entity_facts, _SPEC_BY_KEY[key], anchor, accession=acc)
            if same_filing:
                metrics[key] = next(iter(same_filing.values()))
    if getattr(metrics.get("lt_debt_current"), "concept", None) == "us-gaap:DebtCurrent":
        metrics.pop("short_term_debt", None)   # DebtCurrent already includes it
    if ltd_is_total and getattr(metrics.get("lt_debt_noncurrent"), "concept",
                                None) == "us-gaap:LongTermDebt":
        metrics.pop("lt_debt_current", None)   # the total already holds current maturities
    _compromised_debt_only(entity_facts, metrics, anchor, acc)


def _compromised_debt_only(entity_facts, metrics: dict, anchor: dict, acc) -> None:
    """LiabilitiesSubjectToCompromise also holds trade payables, rejection claims and accrued
    interest. When a LongTermDebt total on the same balance sheet exceeds the debt lines, the
    excess is the compromised debt alone (TSE Q2 2026: 3,024.1 − 1.9 − 589.6 = 2,432.6 of
    the 2,562.3 total)."""
    lstc = metrics.get("debt_subject_to_compromise")
    if getattr(lstc, "concept", None) != "us-gaap:LiabilitiesSubjectToCompromise":
        return
    ltd = next(iter(_select_metric(entity_facts, _LTD, anchor, accession=acc).values()), None)
    lines = sum(metrics[k].numeric_value for k in ("lt_debt_noncurrent", "lt_debt_current")
                if k in metrics)
    if ltd is not None and lines < ltd.numeric_value <= lines + lstc.numeric_value:
        v = ltd.numeric_value - lines
        metrics["debt_subject_to_compromise"] = replace(
            ltd, value=v, numeric_value=v,
            concept="LongTermDebt − LongTermDebtNoncurrent − LongTermDebtCurrent",
            label="Debt subject to compromise (long-term debt less the debt lines)")


def _derive_liabilities(metrics: dict) -> None:
    """Issuers that tag no Liabilities total (AAL, TSE) still balance: liabilities = assets −
    equity − temporary equity. The current and noncurrent subtotals can't stand in: TSE's
    liabilities subject to compromise sit outside both."""
    ta, se = metrics.get("total_assets"), metrics.get("stockholders_equity")
    if "total_liabilities" in metrics or ta is None or se is None:
        return
    mezz = getattr(metrics.get("temporary_equity"), "numeric_value", None) or 0.0
    v = ta.numeric_value - se.numeric_value - mezz
    metrics["total_liabilities"] = replace(
        ta, value=v, numeric_value=v, concept="Assets − StockholdersEquity − TemporaryEquity",
        label="Total liabilities (total assets − equity − temporary equity)")


def _build_anchors(entity_facts, min_year: int, max_year: int) -> dict[int, dt.date]:
    """Map fiscal year -> fiscal-year-end date from annual income/cash-flow periods."""
    for key in _ANCHOR_CONCEPTS:
        spec = _SPEC_BY_KEY[key]
        for concept in spec.concepts:
            facts = _query_concept(entity_facts, concept, spec.statement)
            anchors: dict[int, list] = {}
            for f in facts:
                if getattr(f, "is_dimensioned", False):
                    continue
                d = _duration_days(f)
                pe = getattr(f, "period_end", None)
                if pe is None or d is None or not (_MIN_DAYS <= d <= _MAX_DAYS):
                    continue
                if not (min_year <= pe.year <= max_year):
                    continue
                anchors.setdefault(pe.year, []).append(f)
            if anchors:
                return {y: max(cands, key=lambda fc: _rank(fc, spec.statement)).period_end
                        for y, cands in anchors.items()}
    return {}


def build_financial_series(company, lookback_years: int) -> FinancialSeries:
    """Pull a clean, cited multi-year series of capital-structure facts for an issuer."""
    entity_facts = company.get_facts()
    cik = str(company.cik)
    today = dt.date.today()
    min_year = today.year - lookback_years - 1   # one extra prior year for YoY baselines
    max_year = today.year

    anchors = _build_anchors(entity_facts, min_year, max_year)
    if not anchors:
        return FinancialSeries(cik=cik, years=[])

    selected: dict[str, dict[int, object]] = {}
    for spec in METRIC_SPECS:
        selected[spec.key] = _select_metric(entity_facts, spec, anchors)

    ltd_is_total = _ltd_is_total(entity_facts)
    years: list[YearFacts] = []
    for fy in sorted(anchors):
        yf = YearFacts(fiscal_year=fy, period_end=anchors[fy])
        for key, by_year in selected.items():
            if fy in by_year:
                yf.metrics[key] = by_year[fy]
        _align_debt(entity_facts, yf.metrics, {fy: anchors[fy]}, ltd_is_total)
        _derive_liabilities(yf.metrics)
        years.append(yf)

    # keep the most recent (lookback + 1) fiscal years
    years = years[-(lookback_years + 1):]
    return FinancialSeries(cik=cik, years=years)


# ---------------------------------------------------------------------------
# Quarterly cadence + TTM (Phase 4.6b)
#
# 10-Q XBRL carries quarter and year-to-date durations; Q4 flows are only implicit
# (annual − 9M). The industry-standard TTM at any quarter end is therefore
#     TTM = last annual + current YTD − prior-year YTD
# which needs no derived Q4. Instant (balance-sheet) facts are read directly at
# each quarter end.
# ---------------------------------------------------------------------------

# duration-day buckets: quarter, half, nine-month, year
_BUCKETS = ((80, 100, "Q"), (170, 195, "H"), (255, 290, "N"), (_MIN_DAYS, _MAX_DAYS, "Y"))


def _bucket(days: Optional[int]) -> Optional[str]:
    if days is None:
        return None
    for lo, hi, name in _BUCKETS:
        if lo <= days <= hi:
            return name
    return None


def _near(a: dt.date, b: dt.date, tol: int = 10) -> bool:
    return abs((a - b).days) <= tol


def ttm_from_periods(periods: list[tuple[dt.date, dt.date, float]],
                     asof: dt.date, absent_is_zero: bool = False) -> Optional[float]:
    """Trailing-twelve-month value ending at `asof` from (start, end, value) duration facts.

    Prefers an annual period ending at asof (fiscal Q4); otherwise combines
    annual(prior FY) + YTD(fy start → asof) − YTD(prior year, same span). With
    `absent_is_zero` (add-back charges) any untagged period counts as no charge, the current
    YTD included: issuers tag an impairment line only in the periods that had one, so a Q4
    charge is followed by 10-Qs that carry no line at all. Pure.
    """
    at_asof = [(s, e, v, _bucket((e - s).days)) for s, e, v in periods if _near(e, asof)]
    for s, e, v, b in at_asof:
        if b == "Y":
            return v
    # longest YTD ending at asof (Q1 → "Q", Q2 → "H", Q3 → "N")
    ytds = [(s, e, v, b) for s, e, v, b in at_asof if b in ("Q", "H", "N")]
    if ytds:
        ys, ye, yv, yb = max(ytds, key=lambda t: (t[1] - t[0]).days)
        fy_end = ys - dt.timedelta(days=1)             # fiscal year ends the day before YTD starts
    elif absent_is_zero:
        # no YTD line: the fiscal year end is read off the last annual period instead
        ends = [e for s, e, _v in periods
                if _bucket((e - s).days) == "Y" and 10 < (asof - e).days < 355]
        if not ends:
            return 0.0                                 # nothing tagged in the trailing year
        fy_end, yv = max(ends), 0.0
        yb = _bucket((asof - fy_end).days)
    else:
        return None
    prior_asof = dt.date(asof.year - 1, asof.month, min(asof.day, 28))
    annual = next((v for s, e, v in periods
                   if _near(e, fy_end) and _bucket((e - s).days) == "Y"), None)
    prior_ytd = next((v for s, e, v in periods
                      if _near(e, prior_asof) and _bucket((e - s).days) == yb), None)
    if absent_is_zero:
        annual, prior_ytd = annual or 0.0, prior_ytd or 0.0
    if annual is None or prior_ytd is None:
        return None
    return annual + yv - prior_ytd


@dataclass
class QuarterFacts:
    """One quarter end: instant facts at that date + TTM values for flow metrics."""

    period_end: dt.date
    label: str                                    # e.g. "Q3 2025"
    metrics: dict[str, object] = field(default_factory=dict)   # instant facts (raw edgartools)
    ttm: dict[str, Optional[float]] = field(default_factory=dict)

    def get(self, key: str):
        return self.metrics.get(key)


Periods = list[tuple[dt.date, dt.date, float]]


def _concept_periods(entity_facts, spec: MetricSpec) -> list[Periods]:
    """Clean (start, end, value) duration periods per concept, in the spec's order."""
    out: list[Periods] = []
    for concept in spec.concepts:
        found = set()
        for f in _query_concept(entity_facts, concept, spec.statement):
            if getattr(f, "is_dimensioned", False):
                continue
            v = getattr(f, "numeric_value", None)
            ps, pe = getattr(f, "period_start", None), getattr(f, "period_end", None)
            if v is None or ps is None or pe is None or _bucket((pe - ps).days) is None:
                continue
            found.add((ps, pe, float(v)))
        out.append(sorted(found))
    return out


def _has_interim(facts) -> bool:
    """Any non-dimensioned quarter, half-year or nine-month fact."""
    return any(_bucket(_duration_days(f)) in ("Q", "H", "N") for f in facts
               if not getattr(f, "is_dimensioned", False))


def _floor0(v: Optional[float]) -> Optional[float]:
    return None if v is None else max(v, 0.0)


def _addback_periods(entity_facts, spec: MetricSpec) -> Periods:
    """An add-back's concepts nest (a total contains its parts) and issuers move the charge
    between them from 10-K to 10-Q (ATUS), so each span keeps its largest tagged value."""
    spans: dict[tuple[dt.date, dt.date], float] = {}
    for periods in _concept_periods(entity_facts, spec):
        for ps, pe, v in periods:
            spans[(ps, pe)] = max(v, spans.get((ps, pe), v))
    return sorted((ps, pe, v) for (ps, pe), v in spans.items())


def _ttm_strip(spec: MetricSpec, by_concept: list[Periods],
               quarters: list[dt.date]) -> dict[dt.date, Optional[float]]:
    """TTM at each quarter, each computed inside one concept: an annual from one concept and
    YTDs from another don't add up (AAL: a $2.2bn annual-only D&A tag beside the $1,890M
    statement line). The concept that resolves at the latest quarter leads the whole strip,
    for a statement flow one that also reports interim periods ahead of an annual-only one
    (as _select_metric orders them); a quarter it cannot resolve falls back to the next
    concept that can, as a unit."""
    def lead(p: Periods) -> tuple[bool, bool]:
        annual_only = all(_bucket((e - s).days) == "Y" for s, e, _ in p)
        return ttm_from_periods(p, quarters[-1]) is None, bool(spec.statement) and annual_only
    order = sorted(by_concept, key=lead)
    return {q: next((v for p in order if (v := ttm_from_periods(p, q)) is not None), None)
            for q in quarters}


def quarters_from_periods(periods: list[tuple[dt.date, dt.date, float]]) -> list[tuple[dt.date, float]]:
    """Per-quarter values from duration facts: standalone Q buckets read directly; YTD
    chains (shared period_start) differenced successively (10/25/30/45 → 10/15/5/15).
    NOT TTM — the RP-basket convention is cumulative, not trailing. Pure — unit-tested."""
    out: dict[dt.date, float] = {}
    chains: dict[dt.date, list[tuple[dt.date, float]]] = {}
    for ps, pe, v in periods:
        if _bucket((pe - ps).days) == "Q":
            out[pe] = v
        chains.setdefault(ps, []).append((pe, v))
    for chain in chains.values():   # successive differencing along each YTD chain
        prev = 0.0
        for pe, v in sorted(chain):
            out.setdefault(pe, v - prev)
            prev = v
    return sorted(out.items())


def quarterly_flows(entity_facts, spec: MetricSpec) -> list[tuple[dt.date, float]]:
    """Quarterly (period_end, value) series for a duration metric — the RP-basket
    builder's flow input (Moyer ch. 7). Each quarter is differenced inside one concept, the
    first that yields it."""
    out: dict[dt.date, float] = {}
    for periods in _concept_periods(entity_facts, spec):
        for pe, v in quarters_from_periods(periods):
            out.setdefault(pe, v)
    return sorted(out.items())


def build_quarterly_series(company, lookback_years: int) -> list[QuarterFacts]:
    """Quarter-end series over the lookback: instant metrics at each quarter end,
    TTM for every duration metric. Quarter anchors come from the anchor concepts'
    reported period ends (so we only assert quarters the issuer actually filed)."""
    entity_facts = company.get_facts()
    today = dt.date.today()
    min_date = today.replace(year=today.year - lookback_years)

    anchor_dates: set[dt.date] = set()
    for key in _ANCHOR_CONCEPTS:
        for periods in _concept_periods(entity_facts, _SPEC_BY_KEY[key]):
            anchor_dates |= {e for _s, e, _v in periods if e >= min_date}
        if anchor_dates:
            break
    quarters = sorted(anchor_dates)[-(lookback_years * 4 + 1):]
    if not quarters:
        return []

    # instant metrics: reuse the annual selector with quarter-end anchors
    inst_selected: dict[str, dict] = {}
    anchor_map = {q.toordinal(): q for q in quarters}
    for spec in METRIC_SPECS:
        if spec.kind == "instant":
            inst_selected[spec.key] = _select_metric(entity_facts, spec, anchor_map)

    # TTM per duration metric
    ttm: dict[str, dict[dt.date, Optional[float]]] = {}
    for spec in METRIC_SPECS:
        if spec.kind != "duration":
            continue
        if spec.addback:
            periods = _addback_periods(entity_facts, spec)
            # spans can come from different concepts; a charge never reads as a negative
            ttm[spec.key] = {q: _floor0(ttm_from_periods(periods, q, absent_is_zero=True))
                             if periods else None for q in quarters}
        else:
            ttm[spec.key] = _ttm_strip(spec, _concept_periods(entity_facts, spec), quarters)

    ltd_is_total = _ltd_is_total(entity_facts)
    out: list[QuarterFacts] = []
    for q in quarters:
        # ponytail: calendar-quarter labels; map to fiscal quarters when a non-Dec-FYE name lands
        qf = QuarterFacts(period_end=q, label=f"Q{(q.month - 1) // 3 + 1} {q.year}")
        for key, by_anchor in inst_selected.items():
            if q.toordinal() in by_anchor:
                qf.metrics[key] = by_anchor[q.toordinal()]
        _align_debt(entity_facts, qf.metrics, {q.toordinal(): q}, ltd_is_total)
        _derive_liabilities(qf.metrics)
        qf.ttm = {key: by_q[q] for key, by_q in ttm.items()}
        if qf.metrics or any(v is not None for v in qf.ttm.values()):
            out.append(qf)
    return out


# ---------------------------------------------------------------------------
# Formatting + CitedValue helpers
# ---------------------------------------------------------------------------


def fmt_money_millions(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    millions = value / 1e6
    sign = "-" if millions < 0 else ""
    return f"{sign}${abs(millions):,.0f}M"


def fmt_ratio(value: Optional[float]) -> Optional[str]:
    return None if value is None else f"{value:.1f}x"


def fmt_days(value: Optional[float]) -> Optional[str]:
    return None if value is None else f"{value:.0f} days"


def cited_from_fact(fact, cik: str, kind: str) -> CitedValue:
    """Build a CitedValue from a raw edgartools Fact, pointing at its source filing."""
    val = getattr(fact, "numeric_value", None)
    concept = getattr(fact, "concept", "")
    label = getattr(fact, "label", None)
    pe = getattr(fact, "period_end", None)
    period_phrase = "as of" if kind == "instant" else "for the period ending"
    quote = f"{label or concept} = {fmt_money_millions(val)} ({period_phrase} {pe}) [XBRL]"
    citation = Citation(
        accession_no=getattr(fact, "accession", None),
        form_type=getattr(fact, "form_type", None),
        filing_date=str(getattr(fact, "filing_date", "")) or None,
        section=f"XBRL concept {concept}",
        source_url=index_url_for(cik, getattr(fact, "accession", "")),
        quote=quote,
    )
    return CitedValue(value=val, display=fmt_money_millions(val), citation=citation)


def derived_value(value: Optional[float], formula: str, display: Optional[str],
                  note: Optional[str] = None) -> CitedValue:
    return CitedValue(value=value, display=display, derived=True, formula=formula, note=note)


def metric_kind(key: str) -> str:
    spec = _SPEC_BY_KEY.get(key)
    return spec.kind if spec else "instant"


def cited_metric(year_facts: YearFacts, key: str, cik: str) -> Optional[CitedValue]:
    """CitedValue for a metric on a given fiscal year, or None if that fact is absent."""
    fact = year_facts.get(key)
    if fact is None:
        return None
    return cited_from_fact(fact, cik, metric_kind(key))


def raw_value(year_facts: YearFacts, key: str) -> Optional[float]:
    fact = year_facts.get(key)
    return getattr(fact, "numeric_value", None) if fact else None


def source_url(year_facts: YearFacts, key: str, cik: str) -> Optional[str]:
    """EDGAR filing-index URL for the fact behind a metric (hazard's raw-data table)."""
    fact = year_facts.get(key)
    acc = getattr(fact, "accession", None) if fact else None
    return index_url_for(cik, acc) if acc else None
