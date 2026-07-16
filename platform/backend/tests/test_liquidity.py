"""Liquidity/runway assembly: undrawn sum (no double-count), burn → runway, distress flag,
and the liquidity-event calendar (coupons + maturities vs liquidity)."""
from __future__ import annotations

from app.capstack.liquidity import build_event_calendar, build_liquidity
from app.schemas import (CitedValue, DebtInstrument, ForensicTableRow, MaturityBucket)


def _cv(v):
    return CitedValue(value=v, display=str(v))


def _row(cash=None, fcf=None, ebitda=None, label=None, fy=2026):
    return ForensicTableRow(
        fiscal_year=fy, label=label,
        cash=_cv(cash) if cash is not None else None,
        free_cash_flow=_cv(fcf) if fcf is not None else None,
        ebitda=_cv(ebitda) if ebitda is not None else None,
    )


def _fac(name, outstanding=None, undrawn=None, commitment=None):
    return DebtInstrument(
        instrument=name,
        outstanding=_cv(outstanding) if outstanding is not None else None,
        undrawn=_cv(undrawn) if undrawn is not None else None,
        commitment=_cv(commitment) if commitment is not None else None,
    )


def test_distressed_runway_from_cash_and_undrawn():
    # LCID-shaped: cash 700, undrawn ABL 610 (+ a tiny 9), TTM FCF -4,649, EBITDA -2,154
    ft = [_row(cash=1000e6, fcf=-3.0e9, ebitda=-2.0e9, fy=2025),
          _row(cash=700e6, fcf=-4.649e9, ebitda=-2.154e9, label="Q1 2026")]
    debt = [
        _fac("ABL", outstanding=0, undrawn=610e6, commitment=1000e6),
        _fac("2025 GIB", outstanding=1890e6, undrawn=9e6, commitment=1900e6),
        # commitment-only facility (no tagged undrawn) must NOT be summed into liquidity
        _fac("DDTL", outstanding=0, undrawn=None, commitment=1980e6),
    ]
    wall = [MaturityBucket(year=2028, face=2394e6, instruments=["GIB"]),
            MaturityBucket(year=2026, face=204e6, instruments=["2026 Notes"])]

    lr = build_liquidity(ft, debt, wall, ebitda=-2.154e9)
    assert lr.is_distressed is True
    assert lr.as_of_label == "Q1 2026"          # anchors on the quarter column
    assert lr.cash.value == 700e6
    assert lr.undrawn_committed.value == 619e6  # 610 + 9 only — DDTL commitment excluded
    assert lr.total_liquidity.value == 1319e6
    assert lr.annual_burn.value == 4.649e9
    # runway = 1,319 / (4,649/12) ≈ 3.4 months
    assert abs(lr.runway_months - 1319e6 / (4.649e9 / 12.0)) < 0.05
    assert lr.next_maturity.year == 2026 and lr.next_maturity.face == 204e6


def test_not_distressed_when_ebitda_positive_and_no_runway_without_burn():
    ft = [_row(cash=500e6, fcf=200e6, ebitda=1.5e9, fy=2025)]   # FCF positive → not burning
    lr = build_liquidity(ft, [], [], ebitda=1.5e9)
    assert lr.is_distressed is False
    assert lr.annual_burn is None and lr.runway_months is None
    assert lr.total_liquidity.value == 500e6    # cash alone, no undrawn facilities


def test_none_without_forensic_rows():
    assert build_liquidity([], [], [], ebitda=-1e9) is None


# --------------------------------------------------------------------------- #
# Event calendar (Moyer ch. 8)
# --------------------------------------------------------------------------- #

def _note(name, outstanding, coupon_pct, maturity, facility_type="notes"):
    return DebtInstrument(instrument=name, outstanding=_cv(outstanding),
                          coupon_pct=coupon_pct, maturity=maturity,
                          facility_type=facility_type)


def test_calendar_coupons_and_maturity():
    # LCID-shaped: 1.25% converts due Dec 2026 ($204M), 5.00% notes due March 2030
    sched = [_note("1.25% Convertible Notes due 2026", 204e6, 1.25, "December 2026"),
             _note("5.00% Notes due 2030", 51e6, 5.0, "March 2030")]
    events, note = build_event_calendar(sched, liquidity_total=1319e6, ebitda=-2.154e9,
                                        asof="2026-03-31")
    kinds = {(e.kind, e.instrument) for e in events}
    assert ("maturity", "1.25% Convertible Notes due 2026") in kinds
    coupons = [e for e in events if e.kind == "coupon"]
    assert coupons, "semiannual coupons expected"
    c = next(e for e in coupons if "1.25%" in e.instrument)
    # 204 × 1.25% ÷ 2 = 1.275M
    assert abs(c.amount.value - 1.275e6) < 1e3
    assert "semiannual" in c.assumption
    # coupons stop at the instrument's maturity
    assert all(e.date <= "2026-12" for e in coupons if "1.25%" in e.instrument)
    assert note is None


def test_calendar_flags():
    sched = [_note("Big Notes", 2000e6, 10.0, "June 2027")]
    events, _ = build_event_calendar(sched, liquidity_total=1000e6, ebitda=-1e9,
                                     asof="2026-06-30")
    mat = next(e for e in events if e.kind == "maturity")
    assert "maturity_unfundable" in mat.flags        # 2,000 face > 1,000 liquidity
    coup = next(e for e in events if e.kind == "coupon")
    # 2,000 × 10% ÷ 2 = 100 = 10% of liquidity -> not > 10%; use amount vs threshold
    # payment 100 > 0.10 × 1000 is False (equal); the next test uses a bigger coupon
    sched2 = [_note("Bigger Notes", 3000e6, 10.0, "June 2027")]
    events2, _ = build_event_calendar(sched2, liquidity_total=1000e6, ebitda=-1e9,
                                      asof="2026-06-30")
    coup2 = next(e for e in events2 if e.kind == "coupon")
    assert "coupon_at_risk" in coup2.flags           # 150 > 100 while EBITDA <= 0


def test_calendar_excludes_rateless_maturityless():
    sched = [DebtInstrument(instrument="GIB facility", outstanding=_cv(1890e6)),
             _note("5% Notes due 2030", 100e6, 5.0, "2030")]
    events, note = build_event_calendar(sched, None, None, "2026-03-31")
    assert "1 instrument(s) excluded" in note
    assert all("GIB" not in e.instrument for e in events)


def test_next_event_is_earliest():
    sched = [_note("A Notes", 100e6, 8.0, "December 2027"),
             _note("B Notes", 100e6, 6.0, "July 2026")]
    events, _ = build_event_calendar(sched, 1000e6, 100e6, "2026-06-30")
    assert events == sorted(events, key=lambda e: e.date)
