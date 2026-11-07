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
    series = build_financial_series(_company({
        "Revenues": REVENUE,
        # AAL tags no Liabilities total; a prefix match read LiabilitiesAndStockholdersEquity
        "LiabilitiesAndStockholdersEquity": [_row(64e9, "2024-12-31", *K25)],
        # AAL: InterestExpense through FY2023, InterestExpenseNonoperating after
        "InterestExpense": [_row(50e6, "2023-12-31", *K24, start="2023-01-01")],
        "InterestExpenseNonoperating": [_row(60e6, "2024-12-31", *K25, start="2024-01-01")],
    }), 10)
    assert series.value(2024, "total_liabilities") is None
    assert [series.value(y, "interest_expense") for y in (2023, 2024)] == [50e6, 60e6]


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
