"""XBRL metric selection over SEC companyfacts-shaped JSON, parsed by edgartools itself."""
from __future__ import annotations

from types import SimpleNamespace

from edgar.entity.parser import EntityFactsParser

from app.edgar.facts import build_financial_series, build_quarterly_series

K24 = ("0001811210-25-000010", "2025-02-25")   # (accession, filed) of the FY2024 10-K
K25 = ("0001811210-26-000012", "2026-02-24")   # the FY2025 10-K


def _row(val, end, accn, filed, start=None, form="10-K"):
    row = {"end": end, "val": val, "accn": accn, "fy": int(filed[:4]) - 1, "fp": "FY",
           "form": form, "filed": filed}
    if start:
        row["start"] = start
    return row


def _company(concepts: dict):
    facts = {"us-gaap": {c: {"label": c, "units": {"USD": rows}} for c, rows in concepts.items()}}
    ef = EntityFactsParser.parse_company_facts({"cik": 1811210, "entityName": "X",
                                                "facts": facts})
    return SimpleNamespace(cik=1811210, get_facts=lambda: ef)


REVENUE = [_row(1e9, f"{y}-12-31", *K25, start=f"{y}-01-01") for y in (2023, 2024)]


def test_concepts_match_exactly_and_fall_back_per_year():
    from app.hazard.features import year_features

    series = build_financial_series(_company({
        "Revenues": REVENUE,
        # AAL tags no Liabilities total; a prefix match read LiabilitiesAndStockholdersEquity
        "LiabilitiesAndStockholdersEquity": [_row(61774e6, "2024-12-31", *K25)],
        "Assets": [_row(61774e6, "2024-12-31", *K25)],
        "StockholdersEquity": [_row(-3727e6, "2024-12-31", *K25)],
        # AAL: InterestExpense through FY2023, InterestExpenseNonoperating after
        "InterestExpense": [_row(50e6, "2023-12-31", *K24, start="2023-01-01")],
        "InterestExpenseNonoperating": [_row(60e6, "2024-12-31", *K25, start="2024-01-01")],
    }), 10)
    # derived from the balance: assets − equity (= AAL's current + noncurrent liabilities)
    assert series.value(2024, "total_liabilities") == 65501e6
    fy24 = next(y for y in series.years if y.fiscal_year == 2024)
    assert abs(year_features(fy24)["equity_to_liabilities"] - -3727 / 65501) < 1e-12  # Altman input
    assert [series.value(y, "interest_expense") for y in (2023, 2024)] == [50e6, 60e6]


def test_ttm_and_fiscal_year_read_one_da_concept():
    # AAL: an annual-only, rounded D&A tag ranks ahead of the quarterly statement line; a TTM
    # built from the $2.2bn annual and the line's YTDs matches neither definition
    k, q = ("0000006201-26-000010", "2026-02-18"), ("0000006201-26-000050", "2026-10-22")
    fy = dict(start="2025-01-01")
    ytd, prior = dict(start="2026-01-01", form="10-Q"), dict(start="2025-01-01", form="10-Q")
    co = _company({
        "Revenues": [_row(54e9, "2025-12-31", *k, **fy), _row(41e9, "2026-09-30", *q, **ytd),
                     _row(40e9, "2025-09-30", *q, **prior)],
        "DepreciationDepletionAndAmortization": [_row(2200e6, "2025-12-31", *k, **fy)],
        "DepreciationAndAmortization": [_row(1890e6, "2025-12-31", *k, **fy),
                                        _row(1446e6, "2026-09-30", *q, **ytd),
                                        _row(1418e6, "2025-09-30", *q, **prior)],
    })
    assert build_financial_series(co, 10).value(2025, "d_and_a") == 1890e6
    q3 = next(x for x in build_quarterly_series(co, 10) if x.label == "Q3 2026")
    assert q3.ttm["d_and_a"] == 1918e6                 # 1,890 + 1,446 − 1,418


def test_debt_components_come_from_one_balance_sheet():
    # LCID's FY2024 $126M is ShortTermBorrowings in that 10-K and LongTermDebtCurrent in the
    # FY2025 10-K's comparative column; summing both counts it twice
    series = build_financial_series(_company({
        "Revenues": REVENUE,
        "LongTermDebtNoncurrent": [_row(2002e6, "2024-12-31", *K24),
                                   _row(2002e6, "2024-12-31", *K25)],
        "LongTermDebtCurrent": [_row(126e6, "2024-12-31", *K25)],
        "ShortTermBorrowings": [_row(126e6, "2024-12-31", *K24)],
    }), 10)
    fy24 = next(y for y in series.years if y.fiscal_year == 2024)
    assert sorted(fy24.metrics) == ["lt_debt_current", "lt_debt_noncurrent", "revenue"]


def test_debt_subject_to_compromise_stays_in_total_debt():
    # TSE after its petition: the pre-petition debt left the debt lines for this one
    from app.capstack.forensic import total_debt

    tse = {
        "Revenues": REVENUE,
        "LongTermDebtNoncurrent": [_row(1.9e6, "2024-12-31", *K25)],
        "LongTermDebtCurrent": [_row(589.6e6, "2024-12-31", *K25)],
        "LiabilitiesSubjectToCompromise": [_row(2562.3e6, "2024-12-31", *K25)],
    }

    def debt(concepts):
        series = build_financial_series(_company(concepts), 10)
        return total_debt(next(y for y in series.years if y.fiscal_year == 2024))[0]

    assert abs(debt(tse) - 3153.8e6) < 1
    # the total also holds payables and accrued interest; TSE's LongTermDebt total holds the
    # debt alone: 2,432.6 of it is subject to compromise
    tse["LongTermDebt"] = [_row(3024.1e6, "2024-12-31", *K25)]
    assert abs(debt(tse) - 3024.1e6) < 1


def test_long_term_debt_total_does_not_add_current_maturities_again():
    # ATUS tags LongTermDebt as the total (26,550.8 = 22,402.9 + 4,147.8): a balance sheet
    # without the noncurrent tag falls back to that total, which holds current maturities
    from app.capstack.forensic import total_debt

    atus = build_financial_series(_company({
        "Revenues": REVENUE,
        "LongTermDebt": [_row(25057.5e6, "2023-12-31", *K24),
                         _row(26550.765e6, "2024-12-31", *K25)],
        "LongTermDebtNoncurrent": [_row(22402.928e6, "2024-12-31", *K25)],
        "LongTermDebtCurrent": [_row(185.5e6, "2023-12-31", *K24),
                                _row(4147.837e6, "2024-12-31", *K25)],
    }), 10)
    assert [round(total_debt(y)[0] / 1e6, 3) for y in atus.years] == [25057.5, 26550.765]
    # AAL tags its noncurrent line as LongTermDebt and no LongTermDebtNoncurrent at all
    aal = build_financial_series(_company({
        "Revenues": REVENUE,
        "LongTermDebt": [_row(24617e6, "2024-12-31", *K25)],
        "LongTermDebtCurrent": [_row(5196e6, "2024-12-31", *K25)],
    }), 10)
    assert total_debt(aal.years[-1])[0] == 29813e6


def test_impairment_is_the_largest_tagged_charge_each_period():
    # ATUS: its FY2025 10-K tags the $1.6bn franchise charge as indefinite-lived intangible
    # impairment beside a $0 goodwill line; its 10-Qs use GoodwillAndIntangibleAssetImpairment
    fy, q1 = dict(start="2024-01-01"), dict(start="2025-01-01")
    co = _company({
        "Revenues": REVENUE + [_row(0.3e9, "2025-03-31", *K25, form="10-Q", **q1)],
        "GoodwillImpairmentLoss": [_row(0, "2024-12-31", *K25, **fy)],
        "ImpairmentOfIntangibleAssetsIndefinitelivedExcludingGoodwill": [
            _row(1611e6, "2024-12-31", *K25, **fy)],
        "GoodwillAndIntangibleAssetImpairment": [
            _row(2700e6, "2025-03-31", *K25, form="10-Q", **q1)],
    })
    assert build_financial_series(co, 10).value(2024, "impairment") == 1611e6
    # TTM at Q1: FY 1,611 + Q1 2,700 − prior-year Q1 (untagged: no charge) = 4,311
    q = next(q for q in build_quarterly_series(co, 10) if q.label == "Q1 2025")
    assert q.ttm["impairment"] == 4311e6
