"""Credit-capacity machinery (Moyer, Distressed Debt Analysis, ch. 6): the cash sweep, its
inputs, the coverage chips."""
import pytest

from app.capstack.capacity import capacity_inputs, coverage_chips, sweep


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


def test_capacity_inputs_from_overview():
    ov = {
        "economic_debt_bridge": {"reported_debt": _cv(300e6), "ebitda": _cv(100e6)},
        "forensic_table": [{"capex": _cv(20e6), "ebitda": _cv(100e6), "total_debt": _cv(300e6)}],
        "debt_schedule": [{"instrument": "Notes", "outstanding": _cv(300e6), "coupon_pct": 10.0}],
    }
    inp = capacity_inputs(ov)
    assert (inp["debt"], inp["ebitda"], inp["capex"]) == (300.0, 100.0, 20.0)
    assert inp["rate"] == pytest.approx(0.10)


def test_capacity_inputs_none_at_negative_ebitda():
    ov = {"economic_debt_bridge": {"reported_debt": _cv(300e6), "ebitda": _cv(-100e6)},
          "forensic_table": [], "debt_schedule": []}
    assert capacity_inputs(ov) is None
