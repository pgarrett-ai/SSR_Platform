"""Economic-debt bridge: plain-EBITDA leverage, no cash/net-debt lines, and the EBITDA box.
Synthetic XBRL facts, no network."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from app.capstack.bridge import build_bridge, build_ebitda_box
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

WALK = dict(net_income=1.0e9, interest_expense=0.5e9, income_tax_expense=0.2e9)


def test_leverage_uses_plain_ebitda():
    bridge, _ = build_bridge(_series(**BASE, operating_lease_cost=2e9), [], None)
    # proxy EBITDA (no net-income walk components): OI 1.5 + D&A 2.2 = 3.7e9
    assert bridge.ebitda.value == 3.7e9
    # economic debt 31e9 (24 debt + 7 op leases) / plain EBITDA — no EBITDAR add-back
    assert abs(bridge.economic_leverage.value - 31e9 / 3.7e9) < 1e-9
    assert "EBITDAR" not in bridge.economic_leverage.formula
    assert abs(bridge.reported_leverage.value - 24e9 / 3.7e9) < 1e-9


def test_ebitda_prefers_net_income_walk():
    bridge, _ = build_bridge(_series(**BASE, **WALK), [], None)
    # NI 1.0 + interest 0.5 + taxes 0.2 + D&A 2.2 = 3.9e9 (walk beats the OI proxy)
    assert bridge.ebitda.value == 3.9e9
    assert "net income" in bridge.ebitda.formula


def test_negative_ebitda_leverage_is_not_meaningful():
    # Cash-burner (LCID-style): OI -4.0 + D&A 2.2 = EBITDA -1.8e9. Debt / negative EBITDA
    # sign-flips into "less levered than reported" — must render n.m., never a number.
    neg = dict(BASE, operating_income=-4.0e9)
    bridge, _ = build_bridge(_series(**neg), [], None)
    assert bridge.ebitda.value < 0
    for lev in (bridge.reported_leverage, bridge.economic_leverage):
        assert lev is not None
        assert lev.value is None and lev.display == "n.m."
        assert "negative EBITDA" in lev.note
    # dollar lines untouched — they carry the story for negative-EBITDA issuers
    assert bridge.economic_debt.value == 31e9
    assert bridge.reported_debt.value == 24e9


def test_no_cash_or_net_debt_lines():
    bridge, _ = build_bridge(_series(**BASE, cash=3e9, restricted_cash=1e9), [], None)
    keys = [ln.key for ln in bridge.lines]
    assert "cash_offset" not in keys and "net_economic_debt" not in keys
    assert keys[-1] == "economic_debt"       # the waterfall ends at economic debt
    assert bridge.economic_debt.value == 31e9


# ---- EBITDA box ------------------------------------------------------------------


def test_ebitda_box_walk_and_addbacks():
    series = _series(**BASE, **WALK, share_based_comp=0.3e9)
    cats = ["stock-based compensation", "business optimization costs",
            "depreciation and amortization"]
    box = build_ebitda_box(series, cats)
    assert [ln.key for ln in box.lines] == [
        "net_income", "interest_expense", "income_tax_expense", "d_and_a", "ebitda"]
    assert box.ebitda.value == 3.9e9                       # same rule as the bridge
    # D&A category is a walk line, so it's dropped; stock comp quantifies from XBRL
    assert [a.category for a in box.addbacks] == [
        "stock-based compensation", "business optimization costs"]
    assert box.addbacks[0].amount.value == 0.3e9
    assert box.addbacks[0].amount.citation is not None
    assert box.addbacks[1].amount is None                  # disclosed, not quantifiable


def test_ebitda_box_requires_walk_anchors():
    assert build_ebitda_box(_series(**BASE), ["stock comp"]) is None   # no net_income


# ---- OBS tax effects (unchanged behavior) ----------------------------------------


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
