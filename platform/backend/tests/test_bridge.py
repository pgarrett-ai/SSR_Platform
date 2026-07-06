"""Phase 4.1: EBITDAR-consistent economic leverage. Synthetic XBRL facts, no network."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from app.capstack.bridge import build_bridge
from app.edgar.facts import FinancialSeries, YearFacts


def _fact(value, concept):
    return SimpleNamespace(
        numeric_value=value, concept=concept, label=concept,
        period_end=dt.date(2025, 12, 31), accession="0000000000-26-000001",
        form_type="10-K", filing_date=dt.date(2026, 2, 18),
    )


def _series(**metrics) -> FinancialSeries:
    yf = YearFacts(fiscal_year=2025, period_end=dt.date(2025, 12, 31),
                   metrics={k: _fact(v, k) for k, v in metrics.items()})
    return FinancialSeries(cik="6201", years=[yf])


BASE = dict(lt_debt_noncurrent=24e9, op_lease_noncurrent=6e9, op_lease_current=1e9,
            operating_income=1.5e9, d_and_a=2.2e9)


def test_economic_leverage_uses_ebitdar_when_rent_found():
    bridge, _ = build_bridge(_series(**BASE, operating_lease_cost=2e9), [], None)
    assert bridge.ebitdar.value == 3.7e9 + 2e9
    # economic debt 31e9 (24 debt + 7 op leases) / EBITDAR 5.7e9
    assert abs(bridge.economic_leverage.value - 31e9 / 5.7e9) < 1e-9
    assert "EBITDAR" in bridge.economic_leverage.formula
    # reported leverage stays vs plain EBITDA (reported debt has no leases)
    assert abs(bridge.reported_leverage.value - 24e9 / 3.7e9) < 1e-9
    assert "EBITDAR" not in bridge.reported_leverage.formula


def test_economic_leverage_falls_back_to_ebitda_without_rent():
    bridge, _ = build_bridge(_series(**BASE), [], None)
    assert bridge.ebitdar is None
    assert abs(bridge.economic_leverage.value - 31e9 / 3.7e9) < 1e-9
    assert "not found" in bridge.economic_leverage.note


def test_no_ebitdar_when_no_operating_leases_in_bridge():
    metrics = {k: v for k, v in BASE.items() if not k.startswith("op_lease")}
    bridge, _ = build_bridge(_series(**metrics, operating_lease_cost=2e9), [], None)
    assert bridge.ebitdar is None      # nothing in the numerator -> no add-back
    assert abs(bridge.economic_leverage.value - 24e9 / 3.7e9) < 1e-9


# ---- Phase 4.2: net-debt offsets + per-item tax effect --------------------------------


def _keys(bridge):
    return [ln.key for ln in bridge.lines]


def test_net_debt_offsets_and_total():
    bridge, _ = build_bridge(_series(**BASE, cash=3e9, restricted_cash=1e9), [], None)
    assert _keys(bridge)[-3:] == ["cash_offset", "restricted_cash_offset", "net_economic_debt"]
    cash_line = next(ln for ln in bridge.lines if ln.key == "cash_offset")
    assert cash_line.amount.value == -3e9 and cash_line.amount.citation is not None
    # economic 31e9 − 4e9 offsets
    assert bridge.net_economic_debt.value == 27e9
    assert bridge.economic_debt.value == 31e9   # gross total untouched by offsets


def test_restricted_cash_skipped_when_cash_tag_bundles_it():
    yf = YearFacts(fiscal_year=2025, period_end=dt.date(2025, 12, 31), metrics={
        **{k: _fact(v, k) for k, v in BASE.items()},
        "cash": _fact(4e9, "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
        "restricted_cash": _fact(1e9, "RestrictedCashAndCashEquivalents"),
    })
    bridge, _ = build_bridge(FinancialSeries(cik="6201", years=[yf]), [], None)
    assert "restricted_cash_offset" not in _keys(bridge)   # double-count guard
    assert bridge.net_economic_debt.value == 31e9 - 4e9


def test_no_net_line_without_cash():
    bridge, _ = build_bridge(_series(**BASE), [], None)
    assert bridge.net_economic_debt is None
    assert "net_economic_debt" not in _keys(bridge)


def _obs(amount=1e9, category="pension_opeb"):
    from app.capstack.obs_llm import ObsExtraction
    return ObsExtraction(category=category, label="Pension deficit", amount_usd=amount,
                         amount_text="$1.0B", period=None, recourse="unknown",
                         include_in_bridge=True, bridge_rationale=None,
                         section="Pension footnote", quote="deficit of $1.0 billion")


def test_obs_items_gain_tax_effect_from_direct_etr():
    series = _series(**BASE, effective_tax_rate=0.25)
    _, obs = build_bridge(series, [_obs()], None)
    item = obs[0]
    assert item.tax_effect.value == 0.25e9
    assert item.net.value == 0.75e9
    assert "effective tax rate 25.0%" in item.tax_effect.formula


def test_etr_derived_fallback_and_nol_guard():
    from app.capstack.bridge import _effective_tax_rate
    yf = _series(**BASE, income_tax_expense=0.28e9, pretax_income=1e9).latest()
    etr = _effective_tax_rate(yf, "6201")
    assert abs(etr.value - 0.28) < 1e-9 and etr.derived
    # NOL year: negative pre-tax income -> no meaningful rate -> no tax effects
    yf2 = _series(**BASE, income_tax_expense=0.1e9, pretax_income=-2e9).latest()
    assert _effective_tax_rate(yf2, "6201") is None
    _, obs = build_bridge(_series(**BASE), [_obs()], None)
    assert obs[0].tax_effect is None and obs[0].net is None
