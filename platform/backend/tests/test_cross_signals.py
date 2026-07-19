"""Phase 3 cross-module signals: capstack snapshot cache -> hazard composite inputs."""
from __future__ import annotations

from app.hazard.pipeline import _leverage_to_risk, capstack_signals


def test_leverage_to_risk_mapping():
    assert _leverage_to_risk(2.0) == 10.0
    assert _leverage_to_risk(8.0) == 90.0
    assert _leverage_to_risk(50.0) == 100.0   # clipped high
    assert _leverage_to_risk(0.1) == 0.0      # clipped low


def test_capstack_signals_from_shipped_snapshot():
    # Structural assertions only — the AAL snapshot refreshes on every live run, so exact
    # values would rot; the mapping itself is pinned in test_leverage_to_risk_mapping.
    cs = capstack_signals("AAL")
    assert cs["hidden_leverage"]["raw"] > 0
    assert 0.0 <= cs["hidden_leverage"]["risk"] <= 100.0


def test_capstack_signals_cache_miss_is_empty():
    assert capstack_signals("ZZZNOTREAL") == {}


def test_year_citations_drilldown():
    # Fake XBRL facts: single-fact metrics cite the filing; composites carry the formula
    # as the quote and link to the primary component's filing.
    import datetime as dt
    from types import SimpleNamespace

    from app.edgar.facts import YearFacts
    from app.hazard.features import year_citations

    def fact(value, concept):
        return SimpleNamespace(
            numeric_value=value, concept=concept, label=concept,
            period_end=dt.date(2025, 12, 31), accession="0000006201-26-000014",
            form_type="10-K", filing_date=dt.date(2026, 2, 18),
        )

    yf = YearFacts(fiscal_year=2025, period_end=dt.date(2025, 12, 31), metrics={
        "revenue": fact(54e9, "Revenues"),
        "cash": fact(1e9, "Cash"),
        "lt_debt_noncurrent": fact(24e9, "LongTermDebtNoncurrent"),
        "lt_debt_current": fact(4e9, "LongTermDebtCurrent"),
        "operating_cash_flow": fact(3e9, "NetCashProvidedByUsedInOperatingActivities"),
        "capex": fact(2e9, "PaymentsToAcquirePropertyPlantAndEquipment"),
    })
    cited = year_citations(yf, "6201")

    assert cited["revenue"]["citation"]["form_type"] == "10-K"
    assert "sec.gov" in cited["revenue"]["citation"]["source_url"]
    td = cited["total_debt"]
    assert td["value"] == 28e9 and td["derived"] is True
    assert "lt_debt_noncurrent=$24,000M" in td["citation"]["quote"]
    assert "sec.gov" in td["citation"]["source_url"]      # composites still link
    assert cited["fcf"]["value"] == 1e9
    assert "−" in cited["fcf"]["formula"]                 # ocf − capex
    assert "net_income" not in cited                       # absent fact -> no citation


def _bridge_overview(econ_debt, ebitda, lev=None):
    from app.schemas import CitedValue, EconomicDebtBridge, IssuerHeader, Overview
    return Overview(
        header=IssuerHeader(ticker="BURN", years=3),
        economic_debt_bridge=EconomicDebtBridge(
            economic_debt=CitedValue(value=econ_debt),
            ebitda=CitedValue(value=ebitda),
            economic_leverage=CitedValue(value=lev) if lev is not None else None))


def test_capstack_signals_cash_burner_not_dropped(monkeypatch):
    # LCID shape: bridge stored NO leverage ratio (EBITDA < 0) — pre-fix this returned {}.
    monkeypatch.setattr("app.core.cache.load_latest_overview",
                        lambda t: _bridge_overview(6.85e9, -2.15e9))
    cs = capstack_signals("BURN")
    assert cs["hidden_leverage"]["risk"] == 90.0
    assert cs["hidden_leverage"]["raw"] is None       # no meaningful ratio to display
    assert "EBITDA" in cs["hidden_leverage"]["note"]


def test_capstack_signals_negative_ratio_not_dropped(monkeypatch):
    # ATUS shape: a negative ratio WAS stored (−98.9x) — the lev > 0 guard dropped it.
    monkeypatch.setattr("app.core.cache.load_latest_overview",
                        lambda t: _bridge_overview(26.6e9, -0.27e9, lev=-98.9))
    assert capstack_signals("BURN")["hidden_leverage"]["risk"] == 90.0


def test_capstack_signals_net_cash_stays_quiet(monkeypatch):
    # Positive EBITDA, negative economic debt (true net cash): still no flag.
    monkeypatch.setattr("app.core.cache.load_latest_overview",
                        lambda t: _bridge_overview(-1.0e9, 2.0e9, lev=-0.5))
    assert capstack_signals("BURN") == {}


def test_cash_burner_year_features_sign_safe():
    # C3 regression: negative EBITDA must not yield a negative (monotone-"safe")
    # net_debt_to_ebitda; the burner carries a runway feature instead.
    import datetime as dt
    from types import SimpleNamespace

    import pytest

    from app.edgar.facts import YearFacts
    from app.hazard.features import year_features

    def fact(v):
        return SimpleNamespace(numeric_value=v)

    burner = YearFacts(fiscal_year=2025, period_end=dt.date(2025, 12, 31), metrics={
        "lt_debt_noncurrent": fact(2.0e9), "cash": fact(0.5e9),
        "operating_income": fact(-2.5e9), "d_and_a": fact(0.4e9),
        "operating_cash_flow": fact(-2.0e9), "capex": fact(1.0e9),
        "total_assets": fact(9.0e9),
    })
    f = year_features(burner)
    assert f["ebitda"] == pytest.approx(-2.1e9)
    assert f["net_debt_to_ebitda"] is None            # pre-fix: −0.71 → "safe"
    assert f["runway_years"] == pytest.approx(0.5e9 / 3.0e9)   # cash / |OCF − capex|

    healthy = YearFacts(fiscal_year=2025, period_end=dt.date(2025, 12, 31), metrics={
        "lt_debt_noncurrent": fact(2.0e9), "cash": fact(0.5e9),
        "operating_income": fact(1.0e9), "d_and_a": fact(0.2e9),
        "operating_cash_flow": fact(1.5e9), "capex": fact(0.5e9),
    })
    h = year_features(healthy)
    assert h["net_debt_to_ebitda"] == pytest.approx(1.5e9 / 1.2e9)  # classic ratio survives
    assert h["runway_years"] is None                  # FCF-positive: not the binding constraint
