"""Phase 4.3: XBRL tie-out reconciliation. Synthetic facts, no network."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from app.capstack.reconcile import build_tie_outs, pension_tie_out
from app.edgar.facts import FinancialSeries, YearFacts
from app.capstack.obs_llm import ObsExtraction
from app.schemas import CitedValue, DebtInstrument


def _fact(value, concept):
    return SimpleNamespace(numeric_value=value, concept=concept, label=concept,
                           period_end=dt.date(2025, 12, 31), accession="0000000000-26-000001",
                           form_type="10-K", filing_date=dt.date(2026, 2, 18))


def _series(**metrics):
    yf = YearFacts(fiscal_year=2025, period_end=dt.date(2025, 12, 31),
                   metrics={k: _fact(v, k) for k, v in metrics.items()})
    return FinancialSeries(cik="6201", years=[yf])


def _obs(cat, amt):
    return ObsExtraction(category=cat, label=cat, amount_usd=amt, amount_text=None, period=None,
                         recourse="unknown", include_in_bridge=True, bridge_rationale=None,
                         section=None, quote="q")


def _debt(amt):
    return DebtInstrument(instrument="Notes", outstanding=CitedValue(value=amt))


def _by_label(tie_outs, label):
    return next((t for t in tie_outs if t.label.startswith(label)), None)


LEASES = dict(op_lease_noncurrent=6e9, op_lease_current=1e9)  # XBRL lease total 7e9


def test_lease_within_5pct_ties_out():
    tie_outs, warnings = build_tie_outs(_series(**LEASES), [_obs("lease_operating", 7.1e9)], [])
    t = _by_label(tie_outs, "Leases")
    assert t.status == "match" and t.delta_pct == 1.4
    assert warnings == []


def test_lease_over_5pct_mismatches_and_warns():
    tie_outs, warnings = build_tie_outs(_series(**LEASES), [_obs("lease_operating", 9e9)], [])
    t = _by_label(tie_outs, "Leases")
    assert t.status == "mismatch" and t.delta_pct > 5
    assert any("Leases" in w for w in warnings)


def test_pension_uses_funded_status_magnitude():
    yf = YearFacts(fiscal_year=2025, period_end=dt.date(2025, 12, 31), metrics={
        "pension_benefit_obligation": _fact(-1.5e9, "DefinedBenefitPlanFundedStatusOfPlanAmount"),
    })
    t = pension_tie_out(yf, "6201", 1.55e9)   # deficit vs |funded status| 1.5e9
    assert t is not None and t.status == "match" and abs(t.delta_pct - 3.3) < 0.1


def test_pension_skips_gross_benefit_obligation():
    yf = YearFacts(fiscal_year=2025, period_end=dt.date(2025, 12, 31), metrics={
        "pension_benefit_obligation": _fact(1.5e9, "DefinedBenefitPlanBenefitObligation"),
    })
    assert pension_tie_out(yf, "6201", 1.55e9) is None   # gross ≠ deficit, don't false-flag


def test_debt_schedule_ties_to_reported_debt():
    series = _series(lt_debt_noncurrent=24e9, lt_debt_current=4e9)  # reported 28e9
    tie_outs, _ = build_tie_outs(series, [], [_debt(20e9), _debt(8e9)])  # LLM sum 28e9
    t = _by_label(tie_outs, "Debt schedule")
    assert t.status == "match" and t.delta_pct == 0.0


def test_no_tie_out_without_llm_side():
    tie_outs, warnings = build_tie_outs(_series(**LEASES), [], [])
    assert tie_outs == [] and warnings == []
