"""Liquidity & runway — the distressed-mode framing.

Leverage multiples are meaningless at negative EBITDA (debt / negative EBITDA sign-flips).
For a cash-burner the credit question is instead: how long does the money last? This
bundles the deterministic inputs the platform already computes — cash and tagged undrawn
committed credit (liquidity), free-cash-flow burn (the drain), and the nearest maturity
(the wall) — into a runway estimate. Overview pivots to lead with this when EBITDA <= 0.
"""
from __future__ import annotations

from typing import Optional

from ..edgar.facts import derived_value, fmt_money_millions
from ..schemas import (CitedValue, DebtInstrument, ForensicTableRow, LiquidityRunway,
                       MaturityBucket, NextMaturity)


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
