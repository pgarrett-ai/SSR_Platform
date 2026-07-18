"""capstack.tax382 (Moyer ch. 11): NOL / §382 limitation math. Hand-checked against the
book's P-Corp worked example (notes/13.md): NOL 500, §382 rate 5%, equity FMV 200 -> annual
limit 10 -> over a 20y horizon 200 is usable and 300 is stranded."""
from app.capstack.tax382 import (analyze_tax_asset, section382_limit, tax_asset_pv,
                                 usable_nol)


def test_section382_limit():
    assert section382_limit(200.0, 0.05) == 10.0          # P-Corp: $200 cap × 5% rate
    assert section382_limit(-50.0, 0.05) == 0.0           # bankrupt: pre-COO equity ~0 -> 0 limit
    assert section382_limit(200.0, 0.0) == 0.0


def test_usable_and_stranded_pcorp():
    limit = section382_limit(200.0, 0.05)                 # 10/yr
    assert usable_nol(500.0, limit, 20) == 200.0          # 10 × 20 = 200 usable
    assert 500.0 - usable_nol(500.0, limit, 20) == 300.0  # 300 stranded — the book's number


def test_usable_capped_by_nol_not_limit():
    # small NOL, big limit -> all of it is usable (cap is the NOL, not limit×horizon)
    assert usable_nol(50.0, 100.0, 20) == 50.0


def test_pv_undiscounted_equals_shield():
    # r = 0 -> PV = usable × tax_rate exactly: 200 usable × 20% = 40
    assert abs(tax_asset_pv(500.0, 10.0, 0.20, 20, 0.0) - 40.0) < 1e-9


def test_pv_discounts_below_undiscounted():
    pv = tax_asset_pv(500.0, 10.0, 0.20, 20, 0.10)
    assert 0.0 < pv < 40.0                                # discounting shrinks it
    # annuity check: 10×0.2 per year for 20y at 10% = 2.0 × 8.5136 ≈ 17.03
    assert abs(pv - 2.0 * 8.513564) < 0.05


def test_pv_negative_discount_guarded():
    # discount_rate = −100% would divide by zero; it's clamped to 0 -> PV equals the shield
    assert abs(tax_asset_pv(500.0, 10.0, 0.20, 20, -1.0) - 40.0) < 1e-9


def test_analyze_tax_asset_pcorp():
    out = analyze_tax_asset(nol=500.0, equity_fmv=200.0, rate=0.05, tax_rate=0.21,
                            horizon_years=20, discount_rate=0.0)
    assert out["annual_limit"] == 10.0
    assert out["usable_nol"] == 200.0
    assert out["stranded_nol"] == 300.0
    assert abs(out["undiscounted_shield"] - 42.0) < 1e-9   # 200 × 21%
    assert abs(out["tax_asset_pv"] - 42.0) < 1e-9          # r=0 -> equals shield
