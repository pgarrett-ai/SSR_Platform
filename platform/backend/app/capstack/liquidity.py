"""Liquidity & runway — the distressed-mode framing.

Leverage multiples are meaningless at negative EBITDA (debt / negative EBITDA sign-flips).
For a cash-burner the credit question is instead: how long does the money last? This
bundles the deterministic inputs the platform already computes — cash and tagged undrawn
committed credit (liquidity), free-cash-flow burn (the drain), and the nearest maturity
(the wall) — into a runway estimate. Overview pivots to lead with this when EBITDA <= 0.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Optional

from ..edgar.facts import derived_value, fmt_money_millions
from ..schemas import (CitedValue, DebtInstrument, ForensicTableRow, LiquidityEvent,
                       LiquidityRunway, MaturityBucket, NextMaturity)


def _val(cv: Optional[CitedValue]) -> Optional[float]:
    return cv.value if cv is not None else None


def build_liquidity(forensic_table: list[ForensicTableRow],
                    debt_schedule: list[DebtInstrument],
                    maturity_wall: list[MaturityBucket],
                    ebitda: Optional[float]) -> Optional[LiquidityRunway]:
    """Assemble the liquidity/runway view from already-computed pieces. Returns None when
    there's no forensic row to anchor on."""
    if not forensic_table:
        return None
    latest = forensic_table[-1]          # quarter column if present, else the latest FY
    as_of = latest.label or f"FY{latest.fiscal_year}"

    cash = latest.cash
    cash_v = _val(cash)

    # Undrawn committed credit = sum of TAGGED remaining-capacity facts only. Issuers tag
    # remaining capacity once per real facility, so this never double-counts the member
    # proliferation the tie-out warning flags (commitment tags do; undrawn tags don't).
    undrawn_parts, undrawn_sum = [], 0.0
    for inst in debt_schedule:
        u = _val(inst.undrawn)
        if u and u > 0:
            undrawn_sum += u
            undrawn_parts.append(f"{inst.instrument} {fmt_money_millions(u)}")
    undrawn = (derived_value(undrawn_sum, " + ".join(undrawn_parts),
                             fmt_money_millions(undrawn_sum),
                             note="Sum of tagged undrawn revolver / facility capacity. "
                                  "Facilities without a separately tagged headroom are "
                                  "excluded — see the debt schedule for their commitments.")
               if undrawn_parts else None)

    total_v = (cash_v or 0) + undrawn_sum if (cash_v is not None or undrawn_parts) else None
    total_liquidity = (
        derived_value(total_v,
                      f"cash {fmt_money_millions(cash_v)}"
                      + (f" + undrawn credit {fmt_money_millions(undrawn_sum)}"
                         if undrawn_parts else ""),
                      fmt_money_millions(total_v),
                      note="Cash and equivalents plus tagged undrawn committed credit.")
        if total_v is not None else None)

    # Burn: free cash flow when it's an outflow. In a quarter column FCF is already TTM.
    fcf_v = _val(latest.free_cash_flow)
    annual_burn = None
    runway_months = None
    if fcf_v is not None and fcf_v < 0:
        burn = -fcf_v
        annual_burn = derived_value(
            burn, f"|free cash flow| ({as_of})", fmt_money_millions(burn),
            note="Annual cash burn = negative free cash flow (OCF − capex).")
        if total_v is not None and burn > 0:
            runway_months = round(total_v / (burn / 12.0), 1)

    next_maturity = None
    if maturity_wall:
        nearest = min(maturity_wall, key=lambda b: b.year)
        next_maturity = NextMaturity(year=nearest.year, face=nearest.face,
                                     instruments=list(nearest.instruments))

    return LiquidityRunway(
        is_distressed=(ebitda is not None and ebitda <= 0),
        as_of_label=as_of,
        cash=cash,
        undrawn_committed=undrawn,
        total_liquidity=total_liquidity,
        annual_burn=annual_burn,
        runway_months=runway_months,
        next_maturity=next_maturity,
    )


# --------------------------------------------------------------------------- #
# Liquidity-event calendar (Moyer ch. 8: covenant / coupon / maturity events)
# --------------------------------------------------------------------------- #

_MONTHS = {m.lower(): i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}
_YEAR_RE = re.compile(r"(19|20)\d{2}")


def _parse_maturity(maturity: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    """(year, month|None). Range-spread strings ('2026 to 2038') return (None, None) —
    those amortize across the wall, not at a point date."""
    if not maturity:
        return None, None
    years = [m.group(0) for m in _YEAR_RE.finditer(maturity)]
    if not years or len(set(years)) > 1:
        return None, None
    month = None
    low = maturity.lower()
    for name, num in _MONTHS.items():
        if name in low:
            month = num
            break
    return int(years[0]), month


def build_event_calendar(debt_schedule: list[DebtInstrument],
                         liquidity_total: Optional[float],
                         ebitda: Optional[float],
                         asof: Optional[str],
                         window_months: int = 24) -> tuple[list[LiquidityEvent], Optional[str]]:
    """Every coupon payment and maturity over the next `window_months`, sized against
    total liquidity. Returns (events, excluded-note). Coupon dates are never tagged in
    XBRL, so payment months anchor on the maturity-month anniversary (assumption stated
    per event); frequency: semiannual for notes, quarterly otherwise."""
    from ..fulcrum.adapter import _tranche_coupon

    try:
        start = dt.date.fromisoformat(str(asof)[:10]) if asof else dt.date.today()
    except ValueError:
        start = dt.date.today()
    end = start + dt.timedelta(days=window_months * 30.44)

    events: list[LiquidityEvent] = []
    excluded = 0
    annual_interest = 0.0

    for inst in debt_schedule:
        cv = inst.outstanding or inst.principal
        face = cv.value if cv else None
        rate = _tranche_coupon(inst.model_dump())
        year, month = _parse_maturity(inst.maturity)
        if not face or face <= 0 or (rate <= 0 and year is None):
            excluded += 1
            continue
        annual_interest += (face * rate) if rate > 0 else 0.0

        is_notes = (inst.facility_type or "").lower() == "notes" or "note" in inst.instrument.lower()
        per_year = 2 if is_notes else 4
        freq_label = "semiannual" if per_year == 2 else "quarterly"

        # coupon dates: anniversary months of the maturity month (else spread from as-of)
        if rate > 0:
            anchor_month = month if month else start.month
            payment = face * rate / per_year
            months_between = 12 // per_year
            d = dt.date(start.year, anchor_month, 1)
            while d <= start:
                d = _add_months(d, months_between)
            while d <= end:
                if year is None or (d.year, d.month) <= (year, month or 12):
                    events.append(_coupon_event(inst, d, payment, rate, per_year, freq_label,
                                                month is not None, liquidity_total))
                d = _add_months(d, months_between)

        # maturity event
        if year is not None:
            md = dt.date(year, month or 12, 1)
            if start < md <= end:
                flags = []
                if liquidity_total is not None and face > liquidity_total:
                    flags.append("maturity_unfundable")
                events.append(LiquidityEvent(
                    date=md.strftime("%Y-%m"), kind="maturity", instrument=inst.instrument,
                    amount=derived_value(face, f"face due at maturity ({inst.maturity})",
                                         fmt_money_millions(face)),
                    pct_of_liquidity=(round(100 * face / liquidity_total, 1)
                                      if liquidity_total else None),
                    flags=flags,
                    assumption=None if month else "month unknown — placed at year-end",
                ))

    # coupon_at_risk needs total annualized interest — second pass over coupon events
    thin = (ebitda is not None
            and (ebitda <= 0 or (annual_interest > 0 and ebitda < 2 * annual_interest)))
    for e in events:
        if (e.kind == "coupon" and thin and liquidity_total
                and e.amount.value and e.amount.value > 0.10 * liquidity_total):
            e.flags.append("coupon_at_risk")

    events.sort(key=lambda e: e.date)
    note = (f"{excluded} instrument(s) excluded (no tagged rate or maturity)"
            if excluded else None)
    return events, note


def _coupon_event(inst, d, payment, rate, per_year, freq_label, month_known,
                  liquidity_total):
    return LiquidityEvent(
        date=d.strftime("%Y-%m"), kind="coupon", instrument=inst.instrument,
        amount=derived_value(
            payment,
            f"{fmt_money_millions((inst.outstanding or inst.principal).value)} face × "
            f"{100 * rate:.2f}% ÷ {per_year}",
            fmt_money_millions(payment)),
        pct_of_liquidity=(round(100 * payment / liquidity_total, 1)
                          if liquidity_total else None),
        flags=[],
        assumption=f"{freq_label} assumed"
                   + ("" if month_known else "; months anchored on the as-of date"),
    )


def _add_months(d: dt.date, n: int) -> dt.date:
    m = d.month - 1 + n
    return dt.date(d.year + m // 12, m % 12 + 1, 1)
