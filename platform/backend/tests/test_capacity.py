"""Credit-capacity machinery (Moyer ch. 6): sweep, heatmap, severity, chips."""
import pytest

from app.capstack.capacity import (build_capacity, coverage_chips, heatmap,
                                   severity_slices, sweep)


def test_sweep_hand_checked():
    # debt 300, EBITDA 100, rate 10%, capex 20, g=0:
    # y1: interest 30, available 50, debt_end 250
    # y2: interest 25, available 55, debt_end 195
    run = sweep(300.0, 100.0, 0.10, 20.0, [0.0] * 5)
    assert run["rows"][0]["interest"] == 30.0
    assert run["rows"][0]["available"] == 50.0
    assert run["rows"][0]["debt_end"] == 250.0
    assert run["rows"][1]["debt_end"] == 195.0
    assert run["rows"][0]["leverage"] == 2.5
    assert run["pct_retired"] > 50


def test_moyer_anchor_cells():
    # Table 6-3 anchors (EBITDA 250, capex 125, rate 10%): 2.0x/0% ≈ 92%; 5.0x nearly 0
    grid = heatmap(250.0, 0.10, 0.5)
    levs, gs = grid["leverage"], grid["growth"]
    cell = grid["pct_retired"][levs.index(2.0)][gs.index(0.0)]
    assert abs(cell - 92) < 3
    assert grid["pct_retired"][levs.index(5.0)][gs.index(0.0)] < 5


def test_heatmap_monotonicity():
    grid = heatmap(100.0, 0.09, 0.3)
    for row in grid["pct_retired"]:
        assert all(b >= a - 1e-9 for a, b in zip(row, row[1:]))       # better growth helps
    for col in zip(*grid["pct_retired"]):
        assert all(b <= a + 1e-9 for a, b in zip(col, col[1:]))       # more leverage hurts


def test_severity_flags():
    slices = severity_slices(300.0, 100.0, 0.10, 20.0,
                             wall_by_year=[{"year": 2027, "face": 500.0}])
    worst = next(s for s in slices if s["severity"] == 1.75)
    # −35% EBITDA year: leverage rises even as debt amortizes (or amort hits zero)
    assert any("wall_breach" in f for f in worst["year_flags"])
    assert worst["pct_retired"] < slices[0]["pct_retired"]


def test_coverage_chips_exact():
    chips = coverage_chips(300e6, 100e6, 50e6, 40e6)
    assert chips.debt_ebitda.value == 3.0
    assert chips.debt_ebitda_capex.value == 6.0        # 300 / (100 − 50): capex doubles it
    assert chips.ebitda_interest.value == 2.5
    assert chips.ebitda_capex_interest.value == 1.25
    assert chips.capex_pct_ebitda == 50.0
    assert chips.debt_ebitda.derived is True


def test_chips_nm_guards():
    chips = coverage_chips(300e6, -100e6, 50e6, 40e6)
    assert chips.debt_ebitda.value is None             # negative EBITDA -> n.m.
    assert coverage_chips(None, 100e6, None, None) is None


def _cv(v):
    return {"value": v, "derived": True, "formula": "t"}


def test_build_capacity_from_overview():
    ov = {
        "economic_debt_bridge": {"reported_debt": _cv(300e6), "ebitda": _cv(100e6)},
        "forensic_table": [{"capex": _cv(20e6), "ebitda": _cv(100e6), "total_debt": _cv(300e6)}],
        "debt_schedule": [{"instrument": "Notes", "outstanding": _cv(300e6), "coupon_pct": 10.0}],
        "maturity_wall": [],
    }
    out = build_capacity(ov)
    assert out["available"] is True
    assert out["inputs"]["leverage"] == 3.0
    assert out["inputs"]["rate"] == pytest.approx(0.10)
    assert len(out["severity"]) == 6


def test_build_capacity_nm_at_negative_ebitda():
    ov = {"economic_debt_bridge": {"reported_debt": _cv(300e6), "ebitda": _cv(-100e6)},
          "forensic_table": [], "debt_schedule": [], "maturity_wall": []}
    out = build_capacity(ov)
    assert out["available"] is False and "n.m." in out["note"]
