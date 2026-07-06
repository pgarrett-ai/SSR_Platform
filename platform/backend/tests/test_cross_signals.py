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
    assert 0.0 <= cs["mdna_tone"]["risk"] <= 100.0
    assert cs["mdna_tone"]["risk"] == cs["mdna_tone"]["raw"]


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
