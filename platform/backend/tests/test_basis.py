"""Effective cost basis (Moyer ch. 5): 30/360 day count, coupon-schedule anchoring on the
quote's ISO maturity, accrued/basis/claim/cost math, OID claim at accreted, degradation."""
import datetime as dt

from app.capstack.basis import build_basis, coupon_schedule, days_30_360


def _cv(value):
    return {"value": value, "derived": True, "formula": "test"}


def _ov(schedule, events=None):
    return {"debt_schedule": schedule, "liquidity_events": events or []}


# Moyer's Steelbox worked example: 12% notes bought at 50 with six months accrued.
STEELBOX = [{"instrument": "12% Senior Notes due 2031", "outstanding": _cv(100e6),
             "coupon_pct": 12.0, "maturity": "2031", "secured": False,
             "facility_type": "notes"}]
STEELBOX_BONDS = [{"coupon": 12.0, "maturity": "2031-01-01", "last_price": 50.0,
                   "last_yield": 25.0, "as_of": "2026-07-01"}]


def test_days_30_360():
    assert days_30_360(dt.date(2026, 1, 1), dt.date(2026, 7, 1)) == 180
    assert days_30_360(dt.date(2026, 6, 15), dt.date(2026, 7, 16)) == 31


def test_coupon_schedule_anchors_on_iso_maturity():
    # LCID 2026s: maturity 2026-12-15, semiannual -> Jun-15 / Dec-15
    last, future = coupon_schedule("2026-12-15", 2, dt.date(2026, 7, 16))
    assert last == dt.date(2026, 6, 15)
    assert future == [dt.date(2026, 12, 15)]


def test_steelbox_accrued_basis_claim_cost():
    # coupons Jan-1/Jul-1; settle Jul-1 -> full 180-day period accrued
    out = build_basis(_ov(STEELBOX), STEELBOX_BONDS, settle=dt.date(2026, 7, 1))
    (row,) = out["rows"]
    assert row["accrued"]["value"] == 6.0
    assert row["basis"] == 56.0                          # quote 50 + accrued 6
    assert row["claim_per_100"]["value"] == 106.0        # no OID fields -> 100 + accrued
    assert row["cost_pct_of_claim"] == 52.8
    assert row["accrued"]["derived"] is True and "30/360" in row["accrued"]["formula"]


def test_lcid_2026s_thirty_360_exact():
    sched = [{"instrument": "1.25% Convertible Senior Notes due 2026",
              "outstanding": _cv(1009.9e6), "coupon_pct": 1.25,
              "maturity": "December 2026", "secured": False,
              "seniority": "convertible", "facility_type": "notes"}]
    bonds = [{"coupon": 1.25, "maturity": "2026-12-15", "last_price": 85.25,
              "last_yield": 43.215, "as_of": "2026-07-16"}]
    out = build_basis(_ov(sched), bonds)                 # settle = quote as-of
    (row,) = out["rows"]
    assert row["settle"] == "2026-07-16"
    assert row["accrued"]["value"] == 0.108              # 1.25% × 31/360
    assert row["basis"] == 85.36


def test_oid_claim_at_accreted():
    # Table 5-5 shape: face 100, carrying (accreted) 71.2, quote 65
    sched = [{"instrument": "Senior Discount Notes due 2031", "outstanding": _cv(71.2e6),
              "face_amount": _cv(100e6), "coupon_pct": 5.0, "maturity": "2031",
              "secured": False, "facility_type": "notes"}]
    bonds = [{"coupon": 5.0, "maturity": "2031-03-01", "last_price": 65.0,
              "as_of": "2026-09-01"}]
    out = build_basis(_ov(sched), bonds)
    (row,) = out["rows"]
    assert row["accrued"]["value"] == 2.5                # Mar-1 -> Sep-1 = 180 days at 5%
    assert row["claim_per_100"]["value"] == 73.7         # 71.2 accreted + 2.5 accrued
    assert row["oid"] is True
    assert row["pct_of_accreted"] == 91.3                # 65 × 100/71.2 — not distressed
    assert "502(b)(2)" in row["claim_per_100"]["formula"]


def test_flat_hint_on_coupon_at_risk_and_zero_coupon():
    events = [{"kind": "coupon", "instrument": STEELBOX[0]["instrument"],
               "flags": ["coupon_at_risk"]}]
    out = build_basis(_ov(STEELBOX, events), STEELBOX_BONDS, settle=dt.date(2026, 7, 1))
    assert out["rows"][0]["flat_hint"] is True
    zc = [{"instrument": "Zero Coupon Notes due 2030", "outstanding": _cv(50e6),
           "coupon_pct": 0.0, "maturity": "2030", "secured": False,
           "facility_type": "notes"}]
    zc_bonds = [{"coupon": 0.0, "maturity": "2030-06-01", "last_price": 60.0,
                 "as_of": "2026-07-01"}]
    out = build_basis(_ov(zc), zc_bonds)
    assert out["rows"][0]["flat_hint"] is True           # zero-coupon trades flat


def test_no_quotes_graceful():
    out = build_basis(_ov(STEELBOX), [])
    assert out["rows"] == []
    assert "No matched quotes" in out["note"]
    assert build_basis(_ov([]), [])["rows"] == []
