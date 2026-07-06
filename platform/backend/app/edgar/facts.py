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
from dataclasses import dataclass, field
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


METRIC_SPECS: tuple[MetricSpec, ...] = (
    # --- balance sheet (instant) ---
    MetricSpec("lt_debt_noncurrent", "Long-term debt (noncurrent)", "BalanceSheet", "instant",
               ("LongTermDebtNoncurrent", "LongTermDebt",
                "LongTermDebtAndCapitalLeaseObligations")),
    MetricSpec("lt_debt_current", "Current maturities of long-term debt", "BalanceSheet", "instant",
               ("LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent",
                "DebtCurrent")),
    MetricSpec("short_term_debt", "Short-term borrowings", "BalanceSheet", "instant",
               ("ShortTermBorrowings", "CommercialPaper")),
    MetricSpec("cash", "Cash & equivalents", "BalanceSheet", "instant",
               ("CashAndCashEquivalentsAtCarryingValue",
                "CashAndCashEquivalentsAtCarryingValueIncludingDiscontinuedOperations",
                "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash")),
    MetricSpec("short_term_investments", "Short-term investments", "BalanceSheet", "instant",
               ("ShortTermInvestments",)),
    MetricSpec("restricted_cash", "Restricted cash", "BalanceSheet", "instant",
               ("RestrictedCashAndCashEquivalents", "RestrictedCash",
                "RestrictedCashAndCashEquivalentsNoncurrent", "RestrictedCashNoncurrent",
                "RestrictedCashCurrent")),
    MetricSpec("accounts_payable", "Accounts payable", "BalanceSheet", "instant",
               ("AccountsPayableCurrent", "AccountsPayableTradeCurrent",
                "AccountsPayableAndAccruedLiabilitiesCurrent")),
    MetricSpec("inventory", "Inventory", "BalanceSheet", "instant",
               ("InventoryNet", "AirlineRelatedInventoryNet", "InventoryFinishedGoodsNetOfReserves")),
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
               ("InterestExpense", "InterestExpenseDebt", "InterestAndDebtExpense")),
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
    # --- cash flow (duration) ---
    MetricSpec("d_and_a", "Depreciation & amortization", "CashFlowStatement", "duration",
               ("DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
                "DepreciationAmortizationAndAccretionNet")),
    MetricSpec("operating_cash_flow", "Operating cash flow", "CashFlowStatement", "duration",
               ("NetCashProvidedByUsedInOperatingActivities",)),
    MetricSpec("capex", "Capital expenditures", "CashFlowStatement", "duration",
               ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
                "PaymentsForCapitalImprovements", "PaymentsToAcquireOtherProductiveAssets")),
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
    """Return raw facts for a concept, trying the statement filter first, then unfiltered."""
    base = f"us-gaap:{concept}"
    results = []
    if statement:
        try:
            results = entity_facts.query().by_concept(base).by_statement_type(statement).execute()
        except Exception:
            results = []
    if not results:
        try:
            results = entity_facts.query().by_concept(base).execute()
        except Exception:
            results = []
    return results


def _select_metric(entity_facts, spec: MetricSpec, anchors: dict[int, dt.date]) -> dict[int, object]:
    """For each anchor fiscal year, pick the best fact for this metric (first concept that hits)."""
    for concept in spec.concepts:
        facts = _query_concept(entity_facts, concept, spec.statement)
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
            if year is None:
                continue
            per_year.setdefault(year, []).append(f)
        if per_year:
            return {y: max(cands, key=lambda fc: _rank(fc, spec.statement))
                    for y, cands in per_year.items()}
    return {}


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

    years: list[YearFacts] = []
    for fy in sorted(anchors):
        yf = YearFacts(fiscal_year=fy, period_end=anchors[fy])
        for key, by_year in selected.items():
            if fy in by_year:
                yf.metrics[key] = by_year[fy]
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
                     asof: dt.date) -> Optional[float]:
    """Trailing-twelve-month value ending at `asof` from (start, end, value) duration facts.

    Prefers an annual period ending at asof (fiscal Q4); otherwise combines
    annual(prior FY) + YTD(fy start → asof) − YTD(prior year, same span). Pure — unit-tested.
    """
    at_asof = [(s, e, v, _bucket((e - s).days)) for s, e, v in periods if _near(e, asof)]
    for s, e, v, b in at_asof:
        if b == "Y":
            return v
    # longest YTD ending at asof (Q1 → "Q", Q2 → "H", Q3 → "N")
    ytds = [(s, e, v, b) for s, e, v, b in at_asof if b in ("Q", "H", "N")]
    if not ytds:
        return None
    ys, ye, yv, yb = max(ytds, key=lambda t: (t[1] - t[0]).days)
    fy_end = ys - dt.timedelta(days=1)                 # fiscal year ends the day before YTD starts
    prior_asof = dt.date(asof.year - 1, asof.month, min(asof.day, 28))
    annual = next((v for s, e, v in periods
                   if _near(e, fy_end) and _bucket((e - s).days) == "Y"), None)
    prior_ytd = next((v for s, e, v in periods
                      if _near(e, prior_asof) and _bucket((e - s).days) == yb), None)
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


def _duration_periods(entity_facts, spec: MetricSpec) -> list[tuple[dt.date, dt.date, float]]:
    """All clean duration periods for the first concept that has any."""
    for concept in spec.concepts:
        out = []
        for f in _query_concept(entity_facts, concept, spec.statement):
            if getattr(f, "is_dimensioned", False):
                continue
            v = getattr(f, "numeric_value", None)
            ps, pe = getattr(f, "period_start", None), getattr(f, "period_end", None)
            if v is None or ps is None or pe is None or _bucket((pe - ps).days) is None:
                continue
            out.append((ps, pe, float(v)))
        if out:
            return sorted(set(out))
    return []


def build_quarterly_series(company, lookback_years: int) -> list[QuarterFacts]:
    """Quarter-end series over the lookback: instant metrics at each quarter end,
    TTM for every duration metric. Quarter anchors come from the anchor concepts'
    reported period ends (so we only assert quarters the issuer actually filed)."""
    entity_facts = company.get_facts()
    today = dt.date.today()
    min_date = today.replace(year=today.year - lookback_years)

    anchor_dates: set[dt.date] = set()
    for key in _ANCHOR_CONCEPTS:
        for _s, e, _v in _duration_periods(entity_facts, _SPEC_BY_KEY[key]):
            if e >= min_date:
                anchor_dates.add(e)
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
    dur_periods = {spec.key: _duration_periods(entity_facts, spec)
                   for spec in METRIC_SPECS if spec.kind == "duration"}

    out: list[QuarterFacts] = []
    for q in quarters:
        # ponytail: calendar-quarter labels; map to fiscal quarters when a non-Dec-FYE name lands
        qf = QuarterFacts(period_end=q, label=f"Q{(q.month - 1) // 3 + 1} {q.year}")
        for key, by_anchor in inst_selected.items():
            if q.toordinal() in by_anchor:
                qf.metrics[key] = by_anchor[q.toordinal()]
        for key, periods in dur_periods.items():
            qf.ttm[key] = ttm_from_periods(periods, q) if periods else None
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
