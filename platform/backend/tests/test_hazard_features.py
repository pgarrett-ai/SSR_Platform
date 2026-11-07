"""Hazard feature rows: filing citations and sign-safe leverage for cash burners."""
from __future__ import annotations


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


def test_cash_burner_year_features_sign_safe():
    # negative EBITDA must not yield a negative (monotone-"safe") net_debt_to_ebitda; the
    # burner carries the EBITDA <= 0 flag the trained model reads, and a runway feature.
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
    assert f["net_debt_to_ebitda"] is None            # not −0.71, which would read "safe"
    assert f["ebitda_nonpositive"] == 1.0
    assert f["runway_years"] == pytest.approx(0.5e9 / 3.0e9)   # cash / |OCF − capex|

    healthy = YearFacts(fiscal_year=2025, period_end=dt.date(2025, 12, 31), metrics={
        "lt_debt_noncurrent": fact(2.0e9), "cash": fact(0.5e9),
        "operating_income": fact(1.0e9), "d_and_a": fact(0.2e9),
        "operating_cash_flow": fact(1.5e9), "capex": fact(0.5e9),
    })
    h = year_features(healthy)
    assert h["net_debt_to_ebitda"] == pytest.approx(1.5e9 / 1.2e9)  # classic ratio survives
    assert h["ebitda_nonpositive"] == 0.0
    assert h["runway_years"] is None                  # FCF-positive: not the binding constraint
