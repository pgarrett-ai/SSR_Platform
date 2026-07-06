"""Merton (1974) distance-to-default and default probability.

Equity is a call option on firm assets V struck at the debt face D:

    E      = V*N(d1) - D*e^(-rT)*N(d2)
    sigma_E*E = N(d1)*sigma_V*V            (Ito / Jones-Mason-Rosenfeld identity)

with d1 = [ln(V/D) + (r + 0.5*sigma_V^2)T] / (sigma_V*sqrt(T)),  d2 = d1 - sigma_V*sqrt(T).

We solve the two equations simultaneously for (V, sigma_V) at T=1 given observed equity value E,
equity vol sigma_E, and the default point D, then read off

    DD = [ln(V/D) + (mu - 0.5*sigma_V^2)T] / (sigma_V*sqrt(T)),   PD = N(-DD).

For the risk-neutral PD we take mu = r (the standard Merton/KMV term-structure read), so the
3/6/12-month PDs come straight from N(-d2(T)) using the single solved (V, sigma_V).

The audit flagged raw `fsolve` as fragile; we keep `fsolve` but seed it with the standard
naive guesses (V0 = E + D, sigma_V0 = sigma_E*E/(E+D)), verify the solution, and fall back to
the naive asset proxy on non-convergence rather than returning garbage.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.optimize import fsolve
from scipy.stats import norm


@dataclass
class MertonResult:
    asset_value: float
    asset_vol: float
    converged: bool
    pd_by_horizon: dict[float, float]   # years -> default probability
    dd_1y: float


def _system(x, E, sigma_E, D, r, T):
    V, sigma_V = x
    if V <= 0 or sigma_V <= 1e-6:
        return [1e6, 1e6]
    sqrtT = math.sqrt(T)
    d1 = (math.log(V / D) + (r + 0.5 * sigma_V ** 2) * T) / (sigma_V * sqrtT)
    d2 = d1 - sigma_V * sqrtT
    eq1 = V * norm.cdf(d1) - D * math.exp(-r * T) * norm.cdf(d2) - E
    eq2 = norm.cdf(d1) * sigma_V * V - sigma_E * E
    return [eq1, eq2]


def solve_assets(E: float, sigma_E: float, D: float, r: float = 0.04,
                 T: float = 1.0) -> Optional[tuple[float, float, bool]]:
    """Return (asset_value, asset_vol, converged) or None if inputs are unusable."""
    if not all(np.isfinite([E, sigma_E, D])) or E <= 0 or D <= 0 or sigma_E <= 0:
        return None
    V0 = E + D
    sigma_V0 = sigma_E * E / (E + D)
    try:
        sol, _, ier, _ = fsolve(_system, [V0, sigma_V0], args=(E, sigma_E, D, r, T),
                                full_output=True)
        V, sigma_V = float(sol[0]), float(sol[1])
        converged = ier == 1 and V > 0 and sigma_V > 0 and np.isfinite([V, sigma_V]).all()
    except Exception:
        converged = False
        V, sigma_V = V0, sigma_V0
    if not converged:                       # naive fallback: assets ~ E + D
        V, sigma_V = V0, sigma_V0
    return V, sigma_V, converged


def _pd(V: float, sigma_V: float, D: float, r: float, T: float) -> float:
    dd = (math.log(V / D) + (r - 0.5 * sigma_V ** 2) * T) / (sigma_V * math.sqrt(T))
    return float(norm.cdf(-dd))


def merton(E: float, sigma_E: float, D: float, r: float = 0.04,
           horizons: tuple[float, ...] = (0.25, 0.5, 1.0)) -> Optional[MertonResult]:
    """Full Merton read: solve assets once at T=1, then PD at each horizon."""
    solved = solve_assets(E, sigma_E, D, r, T=1.0)
    if solved is None:
        return None
    V, sigma_V, converged = solved
    pd_by_h = {h: _pd(V, sigma_V, D, r, h) for h in horizons}
    dd_1y = (math.log(V / D) + (r - 0.5 * sigma_V ** 2)) / sigma_V
    return MertonResult(asset_value=V, asset_vol=sigma_V, converged=converged,
                        pd_by_horizon=pd_by_h, dd_1y=float(dd_1y))


def _self_check() -> None:
    # Healthy: big equity cushion, low vol, little debt -> remote default.
    healthy = merton(E=900.0, sigma_E=0.30, D=100.0)
    assert healthy is not None and healthy.converged
    assert healthy.dd_1y > 3.0, healthy.dd_1y
    assert healthy.pd_by_horizon[1.0] < 0.01, healthy.pd_by_horizon

    # Distressed: thin equity, high vol, heavy debt -> material default risk.
    distressed = merton(E=50.0, sigma_E=0.90, D=500.0)
    assert distressed is not None
    assert distressed.pd_by_horizon[1.0] > 0.10, distressed.pd_by_horizon
    assert distressed.dd_1y < healthy.dd_1y

    # Monotonic in leverage: more debt never lowers PD.
    pds = [merton(E=200.0, sigma_E=0.5, D=d).pd_by_horizon[1.0] for d in (100, 300, 600, 900)]
    assert all(b >= a - 1e-9 for a, b in zip(pds, pds[1:])), pds

    # Term structure: longer horizon, higher cumulative PD (for a risky name).
    ts = distressed.pd_by_horizon
    assert ts[0.25] <= ts[0.5] <= ts[1.0], ts

    print("merton self-check OK")
    print(f"  healthy   DD={healthy.dd_1y:.2f}  PD(1y)={healthy.pd_by_horizon[1.0]:.4f}")
    print(f"  distressed DD={distressed.dd_1y:.2f}  PD(3/6/12m)="
          f"{ts[0.25]:.3f}/{ts[0.5]:.3f}/{ts[1.0]:.3f}")


if __name__ == "__main__":
    _self_check()
