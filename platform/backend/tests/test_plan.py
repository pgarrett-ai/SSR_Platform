"""fulcrum.plan (Moyer ch. 12-13): plan-of-reorganization recovery & ROI. The
package per class is valued exogenously (cash + discounted new debt + equity + rights),
divided by the allowed claim, and annualized vs the market entry price. The ROI
per-100-of-face normalization is exercised with a claim != 100 case (the unit bug
the naive plan_value/price form hides at claim == 100)."""
import numpy as np
import pytest

from app.fulcrum.plan import PlanConsideration, evaluate_plan
from app.fulcrum.structure import CapitalStructure, Entity, Tranche


def _one(face=200.0, accrued=10.0):
    # one secured class; explicit accrued so claim = face + accrued regardless of accrual_years
    return CapitalStructure(
        name="Apex", entities=[Entity("OpCo", ev_share=1.0, parent=None)],
        tranches=[Tranche("Senior Notes", "OpCo", face=face, lien_rank=1, secured=True,
                          accrued_interest=accrued)])


def test_recovery_roi_and_delta_hand_checked():
    # claim = 200 face + 10 accrued = 210. Package = cash 40 + new debt 100 @ 0.50 (=50)
    # + 45% of reorg equity (1000-800=200 -> 90) = 180.
    #   recovery %      = 180 / 210          = 85.7%
    #   recovery per100 = 180 / 200 * 100    = 90   (comparable to a bond price)
    #   ROI (entry 60)  = (90/60)^(1/1) - 1  = 50.0%
    #   market at EV 1000: the class is fully covered -> 210 -> 100%; delta = -14.3
    out = evaluate_plan(
        _one(), [PlanConsideration("Senior Notes", cash=40.0, new_debt_face=100.0,
                                   new_debt_haircut=0.50, new_equity_pct=45.0)],
        reorg_ev=1000.0, reorg_debt=800.0, accrual_years=0.0,
        entry_prices={"Senior Notes": 60.0}, duration_years=1.0)
    row = out["rows"][0]
    assert abs(out["reorg_equity_value"]["value"] - 200.0) < 1e-9
    assert abs(row["claim"]["value"] - 210.0) < 1e-9
    assert abs(row["plan_value"]["value"] - 180.0) < 1e-9
    assert abs(row["recovery_pct"]["value"] - 85.7) < 0.05
    assert abs(row["recovery_per_100"]["value"] - 90.0) < 1e-9
    assert abs(row["roi"]["value"] - 50.0) < 0.05           # the per-100 normalization
    assert abs(row["market_pct"]["value"] - 100.0) < 0.05
    assert abs(row["delta_pct"]["value"] - (-14.3)) < 0.05


def test_roi_normalization_is_per_face_not_per_claim():
    # guard the unit error directly: with claim != face, ROI must use per-100-of-face,
    # not recovery_pct (which is per-claim). face 200, accrued 10 -> claim 210.
    out = evaluate_plan(
        _one(), [PlanConsideration("Senior Notes", cash=90.0)],  # plan value 90
        reorg_ev=0.0, reorg_debt=0.0, entry_prices={"Senior Notes": 90.0},
        duration_years=1.0)
    row = out["rows"][0]
    # per-100-of-face = 90/200*100 = 45; ROI = (45/90)-1 = -50% (NOT 90/210*100/90 form)
    assert abs(row["recovery_per_100"]["value"] - 45.0) < 1e-9
    assert abs(row["roi"]["value"] - (-50.0)) < 0.05


def test_new_debt_haircut_required():
    with pytest.raises(ValueError):
        evaluate_plan(_one(), [PlanConsideration("Senior Notes", new_debt_face=100.0)],
                      reorg_ev=500.0, reorg_debt=0.0)


def test_subscription_rights_intrinsic_value():
    # reorg equity 200 over 100 shares -> $2.00/sh; strike 1.50, 10 shares -> intrinsic 5.0
    out = evaluate_plan(
        _one(), [PlanConsideration("Senior Notes", rights_shares=10.0, rights_strike=1.50)],
        reorg_ev=1000.0, reorg_debt=800.0, reorg_shares=100.0)
    assert abs(out["rows"][0]["plan_value"]["value"] - 5.0) < 1e-9
    # no share count -> rights not valued, and a note says so
    out2 = evaluate_plan(
        _one(), [PlanConsideration("Senior Notes", rights_shares=10.0, rights_strike=1.50)],
        reorg_ev=1000.0, reorg_debt=800.0)
    assert out2["rows"][0]["plan_value"]["value"] == 0.0
    assert "rights not valued" in (out2["rows"][0]["plan_value"]["note"] or "")


def test_unquoted_roi_is_none_and_duration_defaults():
    out = evaluate_plan(_one(), [PlanConsideration("Senior Notes", cash=100.0)],
                        reorg_ev=0.0, reorg_debt=0.0)              # no entry_prices
    assert out["rows"][0]["roi"] is None
    assert abs(out["duration_years"] - 14.0 / 12.0) < 1e-9        # ch. 12 benchmark fallback


def test_unknown_target_raises():
    with pytest.raises(ValueError):
        evaluate_plan(_one(), [PlanConsideration("Nope", cash=10.0)],
                      reorg_ev=100.0, reorg_debt=0.0)


def test_negative_package_roi_is_none_not_complex():
    # a negative package must not raise (fractional power of a negative -> complex) — roi None
    out = evaluate_plan(_one(), [PlanConsideration("Senior Notes", cash=-10.0)],
                        reorg_ev=0.0, reorg_debt=0.0, entry_prices={"Senior Notes": 50.0},
                        duration_years=1.0)
    assert out["rows"][0]["roi"] is None
