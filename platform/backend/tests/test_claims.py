"""Phase 4.4: accrued interest + make-whole in the allowed claim."""
from __future__ import annotations

from app.fulcrum import CapitalStructure, Entity, SimConfig, Tranche, analyze


def test_claim_math():
    t = Tranche("N", "Co", face=1000.0, coupon=0.08)
    assert t.accrued(0.0) == 0.0 and t.claim(0.0) == 1000.0
    assert t.accrued(0.5) == 40.0 and t.claim(0.5) == 1040.0     # 1000 × 8% × 0.5
    t2 = Tranche("M", "Co", face=1000.0, coupon=0.08, accrued_interest=25.0, make_whole=30.0)
    assert t2.accrued(0.5) == 25.0                                 # explicit accrued overrides coupon
    assert t2.claim(0.5) == 1055.0                                 # 1000 + 25 + 30


def _structure():
    # EV ≈ 600 covers a 500 first lien; the 200 unsecured takes the residual.
    return CapitalStructure(
        name="T", entities=[Entity("Co", ev_share=1.0)],
        tranches=[Tranche("1L", "Co", 500, lien_rank=1, secured=True, coupon=0.10),
                  Tranche("Unsec", "Co", 200)],
    )


def _near_deterministic(**kw):
    return SimConfig(base_ebitda=120, base_multiple=5.0, stress_prob=0.0,
                     ebitda_vol=0.001, multiple_vol=0.001, n_draws=2000, seed=1, **kw)


def _row(df, name):
    return df.set_index("tranche").loc[name]


def test_accrual_grows_senior_claim_and_squeezes_junior():
    base = analyze(_structure(), _near_deterministic(accrual_years=0.0)).table()
    accr = analyze(_structure(), _near_deterministic(accrual_years=1.0)).table()

    # 1L claim: 500 -> 550 (500 + 500×10%×1); it's made whole on its claim in both.
    assert _row(base, "1L")["claim"] == 500.0
    assert _row(accr, "1L")["claim"] == 550.0
    assert abs(_row(accr, "1L")["mean_recovery_%"] - 100.0) < 0.5

    # Junior recovery falls: (600−500)/200 = 50% -> (600−550)/200 = 25%.
    assert abs(_row(base, "Unsec")["mean_recovery_%"] - 50.0) < 2.0
    assert abs(_row(accr, "Unsec")["mean_recovery_%"] - 25.0) < 2.0


def test_make_whole_also_enters_the_claim():
    struct = CapitalStructure(
        name="T", entities=[Entity("Co", ev_share=1.0)],
        tranches=[Tranche("1L", "Co", 500, lien_rank=1, secured=True, make_whole=50.0),
                  Tranche("Unsec", "Co", 200)],
    )
    df = analyze(struct, _near_deterministic(accrual_years=0.0)).table()
    assert _row(df, "1L")["claim"] == 550.0                        # 500 + 50 make-whole
    assert abs(_row(df, "Unsec")["mean_recovery_%"] - 25.0) < 2.0  # squeezed like the accrual case


def test_zero_accrual_is_identical_to_face():
    df = analyze(_structure(), _near_deterministic(accrual_years=0.0)).table()
    for name in ("1L", "Unsec"):
        assert _row(df, name)["claim"] == _row(df, name)["face"]
