"""Economic-debt bridge: leverage over the canonical EBITDA, no cash/net-debt lines, and the
EBITDA box. Synthetic XBRL facts, no network."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from app.capstack.bridge import build_bridge, build_ebitda_box
from app.capstack.forensic import build_forensic_table, ltm_ebitda
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


def _canonical(series):
    """The EBITDA the pipeline passes in: forensic.ltm_ebitda over the forensic table."""
    return ltm_ebitda(build_forensic_table(series)).ebitda


def test_leverage_divides_by_the_canonical_ebitda():
    series = _series(**BASE, **WALK, operating_lease_cost=2e9)
    bridge, _ = build_bridge(series, [], None, _canonical(series))
    # OI 1.5 + D&A 2.2 = 3.7e9, though the tagged net-income walk would sum to 3.9e9
    assert bridge.ebitda.value == 3.7e9
    # economic debt 31e9 (24 debt + 7 op leases) / plain EBITDA — no EBITDAR add-back
    assert abs(bridge.economic_leverage.value - 31e9 / 3.7e9) < 1e-9
    assert "EBITDAR" not in bridge.economic_leverage.formula
    assert abs(bridge.reported_leverage.value - 24e9 / 3.7e9) < 1e-9
    # debt is a balance-sheet date and EBITDA a trailing period: both are printed
    assert "$24,000M (FY2025) / EBITDA $3,700M (operating income + D&A + impairments, " \
           "FY2025)" in bridge.reported_leverage.formula


def test_spliced_bridge_prints_its_debt_period_and_keeps_ratios_without_ebitda():
    from app.capstack.bridge import renormalize_spliced_bridge

    series = _series(**BASE)
    prior, _ = build_bridge(series, [], None, _canonical(series))
    ratio = prior.reported_leverage.value
    assert renormalize_spliced_bridge(prior, None).reported_leverage.value == ratio
    assert prior.ebitda is not None
    spliced = renormalize_spliced_bridge(prior, _canonical(series), "FY2025")
    assert "reported debt $24,000M (FY2025) / EBITDA" in spliced.reported_leverage.formula


def test_canonical_ebitda_adds_back_impairments():
    # ATUS FY2025: a $1.6bn franchise write-down took operating income to -$113M
    series = _series(**dict(BASE, operating_income=-0.113e9, d_and_a=1.697e9),
                     impairment=1.611e9)
    bridge, _ = build_bridge(series, [], None, _canonical(series))
    assert abs(bridge.ebitda.value - 3.195e9) < 1
    assert bridge.reported_leverage.value is not None       # a ratio, not n.m.


def test_negative_ebitda_leverage_is_not_meaningful():
    # Cash-burner (LCID-style): OI -4.0 + D&A 2.2 = EBITDA -1.8e9. Debt / negative EBITDA
    # sign-flips into "less levered than reported" — must render n.m., never a number.
    neg = _series(**dict(BASE, operating_income=-4.0e9))
    bridge, _ = build_bridge(neg, [], None, _canonical(neg))
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


def test_ebitda_box_reconciles_net_income_to_the_canonical_ebitda():
    # ATUS-style: no interest expense tagged — the box shows the gap as a line
    series = _series(**BASE, net_income=1.0e9, income_tax_expense=0.2e9,
                     share_based_comp=0.3e9)
    cats = ["stock-based compensation", "business optimization costs",
            "depreciation and amortization", "goodwill impairment"]
    box = build_ebitda_box(series.latest(), "6201", cats, _canonical(series))
    assert [ln.key for ln in box.lines] == [
        "net_income", "interest_expense", "income_tax_expense", "d_and_a", "other", "ebitda"]
    assert box.lines[1].label.endswith("not tagged") and box.lines[1].amount is None
    # NI 1.0 + taxes 0.2 + D&A 2.2 + other 0.3 = the bridge's 3.7e9: the box foots
    assert box.ebitda.value == 3.7e9
    assert abs(sum(ln.amount.value for ln in box.lines[:-1] if ln.amount) - 3.7e9) < 1
    # walk lines and impairments (already inside EBITDA) are dropped as add-backs
    assert [a.category for a in box.addbacks] == [
        "stock-based compensation", "business optimization costs"]
    assert box.addbacks[0].amount.value == 0.3e9
    assert box.addbacks[0].amount.citation is not None
    assert box.addbacks[1].amount is None                  # disclosed, not quantifiable


def test_ebitda_box_requires_walk_anchors():
    series = _series(**BASE)                               # no net_income
    assert build_ebitda_box(series.latest(), "6201", ["stock comp"], _canonical(series)) is None


# ---- OBS tax effects (unchanged behavior) ----------------------------------------


def _obs(amount=1e9, category="pension_opeb"):
    from app.capstack.obs_llm import ObsExtraction
    return ObsExtraction(category=category, label="Pension deficit", amount_usd=amount,
                         amount_text="$1.0B", period=None, recourse="unknown",
                         include_in_bridge=True, bridge_rationale=None,
                         section="Pension footnote", quote="deficit of $1.0 billion")


def test_planted_obs_quote_never_reaches_the_bridge(monkeypatch):
    from app.capstack import obs_llm
    from app.edgar.documents import QUOTE_NOT_FOUND, FilingText

    ft = FilingText(accession_no="a", form_type="10-K", filing_date="2026-02-18",
                    period_of_report="2025-12-31", source_url="u", mdna="",
                    notes="Pension plans: the deficit of $1.0 billion is unfunded.")
    monkeypatch.setattr(obs_llm, "extract_structured", lambda **kw: {"items": [
        dict(category="pension_opeb", label="Pension deficit", amount_usd=1e9,
             include_in_bridge=True, quote="the deficit of $1.0 billion"),
        dict(category="supplier_finance", label="Supplier finance", amount_usd=5e9,
             include_in_bridge=True, quote="supplier finance balances of $5.0 billion")]})
    items, err = obs_llm.extract_obs_items(ft)
    assert err is None and [it.grounded for it in items] == [True, False]
    bridge, obs = build_bridge(_series(**BASE), items, ft)
    assert "supplier_finance" not in [ln.key for ln in bridge.lines]   # structural, yet out
    assert bridge.economic_debt.value == 31e9 + 1e9
    assert obs[1].include_in_bridge is False and obs[1].notes == QUOTE_NOT_FOUND


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
