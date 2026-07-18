"""NOL / §382 tax-asset math (Moyer ch. 11 practical; notes/13-14).

§382 caps how much of a company's NOL carryforward it can use each year after an
ownership change: annual limit = long-term tax-exempt rate × equity FMV. The NOL
usable over the carryforward horizon is capped by that annual limit; the excess is
stranded. The tax asset's value is the PV of the tax shield — each year's usable NOL
× marginal tax rate, discounted over the usage schedule.

Bankruptcy note (§382(l)(6)): equity FMV is the POST-reorg value (plan EV − post-reorg
debt); a non-exempt ownership change of a bankrupt firm (pre-COO equity ≈ 0) forfeits
the NOL. Post-2017 federal NOLs carry forward indefinitely — the horizon is a user input.

All $ in $mm. Pure/deterministic; the endpoint wraps outputs in cited derived formulas.
"""
from __future__ import annotations


def section382_limit(equity_fmv: float, rate: float) -> float:
    """Annual NOL-use cap = §382 long-term tax-exempt rate × equity FMV ($mm)."""
    return max(equity_fmv, 0.0) * max(rate, 0.0)


def usable_nol(nol: float, annual_limit: float, horizon_years: int) -> float:
    """NOL usable over the carryforward horizon, capped by the annual limit."""
    return min(max(nol, 0.0), max(annual_limit, 0.0) * max(horizon_years, 0))


def tax_asset_pv(nol: float, annual_limit: float, tax_rate: float,
                 horizon_years: int, discount_rate: float) -> float:
    """PV of the NOL tax shield: each year min(annual_limit, remaining NOL) × tax_rate,
    discounted at discount_rate over the horizon (stops early when the NOL is exhausted)."""
    remaining = max(nol, 0.0)
    limit = max(annual_limit, 0.0)
    disc = max(discount_rate, 0.0)   # a negative discount is nonsensical here (and −100% divides by 0)
    pv = 0.0
    for t in range(1, max(horizon_years, 0) + 1):
        if remaining <= 0 or limit <= 0:
            break
        used = min(limit, remaining)
        pv += used * tax_rate / (1.0 + disc) ** t
        remaining -= used
    return pv


def analyze_tax_asset(nol: float, equity_fmv: float, rate: float, tax_rate: float,
                      horizon_years: int, discount_rate: float) -> dict:
    """Full §382 read for a given NOL and the (user-supplied) rate / equity FMV / tax rate."""
    limit = section382_limit(equity_fmv, rate)
    usable = usable_nol(nol, limit, horizon_years)
    stranded = max(nol, 0.0) - usable
    return {
        "annual_limit": limit,
        "usable_nol": usable,
        "stranded_nol": stranded,
        "undiscounted_shield": usable * tax_rate,
        "tax_asset_pv": tax_asset_pv(nol, limit, tax_rate, horizon_years, discount_rate),
    }
