"""F1/F2 covenant dollars: the Moyer ch. 7 RP-basket template, NI asymmetry,
quarterly-flow YTD differencing, capacity tokens, and the liens-headroom archetypes
including the fixed unbounded gate (must NOT fire on AAL-shaped inputs)."""
import datetime as dt

from app.capstack.covenant_dollars import (build_liens_headroom, parse_capacity_tokens,
                                           rp_basket_from_flows)
from app.edgar.facts import quarters_from_periods

D = dt.date
Q1, Q2, Q3 = D(2025, 9, 30), D(2025, 12, 31), D(2026, 3, 31)


def _cv(v):
    return {"value": v, "derived": True, "formula": "test"}


def _flows(ni=(), eq=(), div=(), buy=()):
    return {"net_income": list(ni), "equity_issuance_proceeds": list(eq),
            "dividends_paid": list(div), "stock_repurchases": list(buy)}


# ---- F1: RP-basket builder -------------------------------------------------------


def test_moyer_template_cumulative_15():
    # starter $5mm ($-token), NI +10/−4/+6 → credits +5/−4/+3, equity +8, dividends −2
    flows = _flows(ni=[(Q1, 10e6), (Q2, -4e6), (Q3, 6e6)],
                   eq=[(Q1, 8e6)], div=[(Q2, 2e6)])
    covs = [{"family_label": "indenture", "baskets": [
        {"name": "Restricted Payments builder basket",
         "value": "the greater of $5,000,000 and 50% of Consolidated Net Income",
         "quote": "quoted covenant text"}]}]
    rp = rp_basket_from_flows(flows, covs, years=3)
    assert rp.available and rp.covenant_status == "extracted"
    assert rp.starter.value == 5.0
    assert [p.contribution for p in rp.points] == [13.0, -6.0, 3.0]
    assert rp.points[-1].cumulative == 10.0
    assert rp.capacity.value == 15.0
    assert "0.5×NI" in rp.capacity.formula
    assert "junior-debt" in rp.capacity.note          # omissions enumerated


def test_ni_asymmetry():
    # 50% credit on profits, 100% deduction on losses (Moyer ch. 7)
    rp = rp_basket_from_flows(_flows(ni=[(Q1, 10e6), (Q2, -10e6)]), [], 3)
    assert rp.points[0].ni_credit == 5.0
    assert rp.points[1].ni_credit == -10.0


def test_no_rp_fact_reads_none_with_unbounded_leakage():
    rp = rp_basket_from_flows(_flows(ni=[(Q1, 10e6)]),
                              [{"baskets": [{"name": "Liens (Section 6.06)"}]}], 3)
    assert rp.covenant_status == "none"
    assert any("unbounded leakage" in n for n in rp.notes)
    assert rp.starter.value == 0.0


def test_missing_concept_zero_leg_noted():
    rp = rp_basket_from_flows(_flows(ni=[(Q1, 10e6)]), [], 3)
    assert rp.points[0].contribution == 5.0            # equity/divs/buybacks legs at 0
    assert "equity_issuance_proceeds" in rp.formula_note


def test_builder_negative_floors_capacity_at_zero():
    rp = rp_basket_from_flows(_flows(ni=[(Q1, -3300e6)]), [], 3)     # the LCID persona
    assert rp.capacity.value == 0.0
    assert "builder negative" in rp.capacity.note


def test_no_quarters_unavailable():
    rp = rp_basket_from_flows(_flows(), [], 3)
    assert rp.available is False


# ---- quarterly_flows: YTD differencing (facts.py groundwork) ---------------------


def test_quarterly_flows_ytd_differencing():
    s = D(2025, 1, 1)
    periods = [(s, D(2025, 3, 31), 10.0), (s, D(2025, 6, 30), 25.0),
               (s, D(2025, 9, 30), 30.0), (s, D(2025, 12, 31), 45.0)]
    assert [v for _, v in quarters_from_periods(periods)] == [10.0, 15.0, 5.0, 15.0]


def test_quarterly_flows_standalone_q_wins():
    s = D(2025, 1, 1)
    periods = [(s, D(2025, 3, 31), 10.0),
               (D(2025, 4, 1), D(2025, 6, 30), 99.0),   # standalone Q2 bucket
               (s, D(2025, 6, 30), 25.0)]
    assert dict(quarters_from_periods(periods))[D(2025, 6, 30)] == 99.0


# ---- token parser ----------------------------------------------------------------


def test_parse_capacity_tokens_fixtures():
    assert parse_capacity_tokens("$1,000,000,000")["dollars"] == 1000.0
    assert parse_capacity_tokens("no more than 55%")["pct"] == 0.55
    assert parse_capacity_tokens("at least 1.6 to 1.0")["ratio"] == 1.6
    stub = parse_capacity_tokens("Governed by Section 6.06; specific capacity not "
                                 "included in provided excerpt")
    assert stub == {"dollars": None, "pct": None, "ratio": None}


# ---- F2: liens-headroom archetypes -----------------------------------------------

AAL_SHAPED = {
    "debt_schedule": [
        {"instrument": "2025 AAdvantage Term Loan Facility", "secured": True},
        # unsecured, but governed by NO extracted family (the PSP-note shape) — the
        # unbounded gate must not fire vacuously (feasibility-critic regression)
        {"instrument": "PSP Promissory Note", "secured": False},
    ],
    "covenants": [
        {"family_label": "credit agreement dated March 24, 2025",
         "governs_instruments": ["2025 AAdvantage Term Loan Facility"],
         "financial_covenants": [
             {"kind": "Maintenance / incurrence LTV Ratio (loan-to-value) test",
              "threshold": "no more than 55%"}],
         "baskets": [
             {"name": "Liens (Section 6.06)",
              "value": "Governed by Section 6.06; specific capacity not included "
                       "in provided excerpt"}],
         "j_crew_blocker_present": True},
        {"family_label": "credit agreement dated December 19, 2024",
         "governs_instruments": [],
         "financial_covenants": [
             {"kind": "Collateral Coverage Ratio (maintenance)",
              "threshold": "At least 1.6 to 1.0"}]},
    ],
    "asset_snapshot": None,
}


def test_aal_shaped_not_unbounded_gate_regression():
    out = build_liens_headroom(AAL_SHAPED)
    assert out["archetype"] != "unbounded"
    assert out["unbounded_instruments"] == []
    archs = {r["archetype"] for r in out["rows"]}
    assert "ratio_only" in archs and "present_unquantified" in archs
    assert out["j_crew_blocker_present"] is True
    ccr = next(r for r in out["rows"] if "Collateral Coverage" in (r["name"] or ""))
    assert ccr["ratio"] == 1.6 and ccr["headroom"] is None


def test_lcid_shaped_unbounded():
    ov = {"debt_schedule": [{"instrument": "2030 Notes", "secured": False,
                             "seniority": "convertible"}],
          "covenants": [{"family_label": "5% notes due 2030",
                         "governs_instruments": ["2030 Notes"],
                         "financial_covenants": [], "baskets": []}]}
    out = build_liens_headroom(ov)
    assert out["archetype"] == "unbounded"
    assert out["unbounded_instruments"] == ["2030 Notes"]
    assert "covenant-lite" in out["unbounded_note"]


def test_nta_computed_headroom_420():
    ov = {"debt_schedule": [],
          "covenants": [{"family_label": "indenture", "governs_instruments": [],
                         "baskets": [{"name": "Liens basket",
                                      "value": "liens not to exceed 10% of consolidated "
                                               "net tangible assets"}]}],
          # goodwill 500 + other intangibles 300 → merged intangibles 800 upstream
          "asset_snapshot": {"total_assets": _cv(5000e6), "intangibles": _cv(800e6)}}
    out = build_liens_headroom(ov)
    row = next(r for r in out["rows"] if r["archetype"] == "computed")
    assert row["headroom"]["value"] == 420.0           # 10% × (5000 − 800)
    assert out["suggested_priming"]["value"] == 420.0


def test_dollar_token_is_stated_capacity_never_headroom():
    ov = {"debt_schedule": [],
          "covenants": [{"family_label": "credit agreement",
                         "baskets": [{"name": "2025 Incremental Term Loans "
                                              "(this amendment)",
                                      "value": "$1,000,000,000"}]}]}
    out = build_liens_headroom(ov)
    r = out["rows"][0]
    assert r["archetype"] == "stated_capacity" and r["headroom"] is None
    assert out["suggested_priming"]["value"] == 1000.0
    assert "utilization unknown" in out["suggested_priming"]["basis"]


def test_no_covenants_unavailable():
    assert build_liens_headroom({"covenants": [], "debt_schedule": []})["available"] is False
