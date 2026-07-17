"""Deterministic EV explorer: breakpoints, monotonicity, coverage, negative EBITDA."""
import numpy as np

from app.fulcrum.explore import explore
from app.fulcrum.structure import CapitalStructure, Entity, Tranche


def _apex():
    return CapitalStructure(
        name="Apex",
        entities=[Entity("OpCo", 1.0, parent="HoldCo"), Entity("HoldCo", 0.0)],
        tranches=[
            Tranche("1L", "OpCo", face=500.0, lien_rank=1, secured=True),
            Tranche("2L", "OpCo", face=250.0, lien_rank=2, secured=True),
            Tranche("OpCo Unsec", "OpCo", face=120.0),
            Tranche("HoldCo Unsec", "HoldCo", face=150.0),
        ],
        admin_fees=30.0,
    )


def test_breakpoints_hand_checked():
    out = explore(_apex(), ebitda=120.0, accrual_years=0.0)
    by = {t["tranche"]: t for t in out["tranches"]}
    # 1L sees value once EV clears the $30 admin fees; covered at 30 + 500
    assert abs(by["1L"]["ev_enters"] - 30.0) < 1.0
    assert abs(by["1L"]["ev_covered"] - 530.0) < 2.0
    assert abs(by["2L"]["ev_covered"] - 780.0) < 2.0
    # HoldCo paper needs every OpCo claim whole first: 30 + 500 + 250 + 120
    assert abs(by["HoldCo Unsec"]["ev_enters"] - 900.0) < 2.0
    assert abs(by["HoldCo Unsec"]["ev_covered"] - 1050.0) < 2.0


def test_recovery_curves_monotone():
    out = explore(_apex(), ebitda=120.0)
    for t in out["tranches"]:
        arr = np.array(t["recovery_pct"])
        assert (np.diff(arr) >= -1e-9).all(), t["tranche"]


def test_coverage_and_breakevens():
    out = explore(_apex(), ebitda=120.0)
    cov = out["coverage"]
    be = out["breakeven_multiples"]
    # senior (secured) claims = 750 -> breakeven 6.25x at EBITDA 120
    assert abs(be["senior"] - 750.0 / 120.0) < 0.01
    assert abs(be["total"] - 1020.0 / 120.0) < 0.01
    # coverage series linear in m: coverage at the breakeven multiple ~= 1.0
    i = cov["multiple"].index(6.25)
    assert abs(cov["senior"][i] - 1.0) < 0.01


def test_negative_ebitda_still_works():
    out = explore(_apex(), ebitda=-3330.0)
    assert out["available"] and out["multiple_grid"] is None
    assert len(out["ev_grid"]) == len(out["tranches"][0]["recovery_pct"])


def test_not_repriced_flag():
    out = explore(_apex(), ebitda=100.0, quotes=[98.0, 95.0])
    # face 1020 > 6x100 and mean quote 96.5 >= 90 -> flag
    assert out["not_repriced"] is True
    out = explore(_apex(), ebitda=200.0, quotes=[98.0])
    assert out["not_repriced"] is False
