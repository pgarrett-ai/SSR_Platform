"""Phase 4.6b: quarterly cadence — TTM core + quarterly timeline/what-changed (pure)."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from app.capstack.timelines import quarterly_leverage_timeline, what_changed_quarterly
from app.edgar.facts import QuarterFacts, ttm_from_periods

D = dt.date


def test_ttm_annual_period_wins():
    periods = [(D(2024, 1, 1), D(2024, 12, 31), 100.0)]
    assert ttm_from_periods(periods, D(2024, 12, 31)) == 100.0


def test_ttm_fy_plus_ytd_minus_prior_ytd():
    periods = [
        (D(2024, 1, 1), D(2024, 12, 31), 100.0),   # FY2024
        (D(2025, 1, 1), D(2025, 9, 30), 90.0),     # 9M 2025
        (D(2024, 1, 1), D(2024, 9, 30), 70.0),     # 9M 2024
    ]
    assert ttm_from_periods(periods, D(2025, 9, 30)) == 120.0   # 100 + 90 − 70


def test_ttm_q1_uses_quarter_bucket():
    periods = [
        (D(2024, 1, 1), D(2024, 12, 31), 100.0),
        (D(2025, 1, 1), D(2025, 3, 31), 30.0),     # Q1 2025 (YTD == Q)
        (D(2024, 1, 1), D(2024, 3, 31), 20.0),
    ]
    assert ttm_from_periods(periods, D(2025, 3, 31)) == 110.0


def test_ttm_missing_prior_ytd_is_none():
    periods = [
        (D(2024, 1, 1), D(2024, 12, 31), 100.0),
        (D(2025, 1, 1), D(2025, 9, 30), 90.0),     # no prior-year 9M
    ]
    assert ttm_from_periods(periods, D(2025, 9, 30)) is None


def _q(label, end, debt=None, cash=None, oi=None, da=0.0, ocf=None, capex=0.0, rev=None):
    metrics = {}
    if debt is not None:
        metrics["lt_debt_noncurrent"] = SimpleNamespace(numeric_value=debt)
    if cash is not None:
        metrics["cash"] = SimpleNamespace(numeric_value=cash)
    return QuarterFacts(period_end=end, label=label, metrics=metrics,
                        ttm={"operating_income": oi, "d_and_a": da,
                             "operating_cash_flow": ocf, "capex": capex, "revenue": rev})


def test_quarter_features_maps_instants_and_ttm():
    from app.hazard.features import quarter_features

    qf = QuarterFacts(
        period_end=D(2025, 9, 30), label="Q3 2025",
        metrics={
            "total_assets": SimpleNamespace(numeric_value=100e9),
            "total_liabilities": SimpleNamespace(numeric_value=80e9),
            "current_assets": SimpleNamespace(numeric_value=30e9),
            "current_liabilities": SimpleNamespace(numeric_value=20e9),
            "retained_earnings": SimpleNamespace(numeric_value=5e9),
            "stockholders_equity": SimpleNamespace(numeric_value=20e9),
            "lt_debt_noncurrent": SimpleNamespace(numeric_value=30e9),
            "lt_debt_current": SimpleNamespace(numeric_value=2e9),
            "shares_outstanding": SimpleNamespace(numeric_value=650e6),
        },
        ttm={"operating_income": 8e9, "d_and_a": 2e9, "revenue": 50e9},
    )
    f = quarter_features(qf)
    assert f["label"] == "Q3 2025" and f["period_end"] == "2025-09-30"
    assert f["total_debt"] == 32e9                       # instants summed
    assert f["ebitda"] == 10e9                           # TTM flows: OI + D&A
    assert f["ebit_to_assets"] == 0.08                   # TTM EBIT / instant assets
    assert f["wc_to_assets"] == 0.10
    assert f["re_to_assets"] == 0.05
    assert f["equity_to_liabilities"] == 0.25
    assert f["shares_outstanding"] == 650e6
    # a missing TTM flow must degrade to None, not raise
    qf.ttm["operating_income"] = None
    assert quarter_features(qf)["ebitda"] is None


def test_quarterly_timeline_and_changes():
    q2 = _q("Q2 2025", D(2025, 6, 30), debt=30e9, cash=1e9, oi=3e9, da=2e9, ocf=3e9, rev=54e9)
    q3 = _q("Q3 2025", D(2025, 9, 30), debt=31e9, cash=0.8e9, oi=2e9, da=2e9, ocf=2e9, rev=53e9)
    pts = quarterly_leverage_timeline([q2, q3])
    assert pts[0].label == "Q2 2025" and pts[0].leverage == 6.0    # 30 / (3+2)
    assert pts[1].leverage == 7.75                                  # 31 / 4

    changes = what_changed_quarterly([q2, q3])
    by = {c.metric: c for c in changes}
    assert by["Leverage (debt/EBITDA TTM)"].direction == "worse"
    assert by["Total reported debt"].direction == "worse"
    assert by["Cash & equivalents"].direction == "worse"
    assert changes[0].prior_label == "Q2 2025" and changes[0].latest_label == "Q3 2025"
    assert what_changed_quarterly([q3]) == []


def test_quarter_forensic_row_snapshot_plus_ttm():
    from app.capstack.forensic import quarter_forensic_row

    qf = QuarterFacts(
        period_end=D(2026, 3, 31), label="Q1 2026",
        metrics={
            "lt_debt_noncurrent": SimpleNamespace(
                numeric_value=2.0e9, concept="LongTermDebtNoncurrent", label="LT debt",
                period_end=D(2026, 3, 31), accession="0000000000-26-000002",
                form_type="10-Q", filing_date=D(2026, 5, 5)),
            "cash": SimpleNamespace(
                numeric_value=0.7e9, concept="Cash", label="Cash",
                period_end=D(2026, 3, 31), accession="0000000000-26-000002",
                form_type="10-Q", filing_date=D(2026, 5, 5)),
            "accounts_payable": SimpleNamespace(
                numeric_value=0.4e9, concept="AccountsPayable", label="AP",
                period_end=D(2026, 3, 31), accession="0000000000-26-000002",
                form_type="10-Q", filing_date=D(2026, 5, 5)),
        },
        ttm={"operating_income": -3.5e9, "d_and_a": 0.5e9, "revenue": 1.4e9,
             "cogs": 2.9e9, "operating_cash_flow": -3.7e9, "capex": 0.95e9},
    )
    row = quarter_forensic_row(qf, cik="1811210")
    assert row.label == "Q1 2026" and row.fiscal_year == 2026
    assert row.total_debt.value == 2.0e9                       # snapshot, not TTM
    assert row.cash.citation is not None                       # instants cite the 10-Q
    assert row.revenue.value == 1.4e9 and row.revenue.derived  # flows are TTM, derived
    assert "TTM" in row.revenue.formula
    assert row.ebitda.value == -3.0e9                          # TTM OI + D&A
    assert row.free_cash_flow.value == -3.7e9 - 0.95e9         # TTM OCF − capex
    assert abs(row.dpo.value - 0.4e9 / 2.9e9 * 365.0) < 1e-9   # AP snapshot / TTM COGS
