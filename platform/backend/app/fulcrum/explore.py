"""Deterministic EV explorer (Moyer ch. 5/6/12): who is in the money at EV = X.

One vectorized run_waterfall call over an EV grid gives every tranche's recovery curve;
the waterfall is piecewise-linear in EV, so breakpoints interpolate exactly between grid
nodes. Works at negative EBITDA (raw-EV axis, multiples omitted) — no SimConfig involved.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from .structure import CapitalStructure
from .waterfall import run_waterfall

_GRID_N = 241
_FULL = 0.999


def _cross(grid: np.ndarray, series: np.ndarray, threshold: float) -> Optional[float]:
    """First EV where series >= threshold, linearly interpolated within the crossing segment."""
    idx = np.argmax(series >= threshold)
    if series[idx] < threshold:
        return None
    if idx == 0:
        return float(grid[0])
    x0, x1, y0, y1 = grid[idx - 1], grid[idx], series[idx - 1], series[idx]
    if y1 == y0:
        return float(x1)
    return float(x0 + (x1 - x0) * (threshold - y0) / (y1 - y0))


def _covered(grid: np.ndarray, pct: np.ndarray) -> Optional[float]:
    """EV where the class is paid its full claim. The saturation kink (pct = 1.0) sits
    between grid nodes; the curve is linear before it, so extrapolate the last rising
    segment forward to 1.0 (exact on the linear piece)."""
    idx = np.argmax(pct >= _FULL)
    if pct[idx] < _FULL:
        return None
    if idx >= 2 and pct[idx - 1] > pct[idx - 2]:
        slope = (pct[idx - 1] - pct[idx - 2]) / (grid[idx - 1] - grid[idx - 2])
        return float(grid[idx - 1] + (1.0 - pct[idx - 1]) / slope)
    return _cross(grid, pct, _FULL)


def _enters(grid: np.ndarray, rec: np.ndarray) -> Optional[float]:
    """EV where the tranche first sees value. The kink sits between grid nodes, and the
    curve is flat-zero before it — so back-extrapolate the first rising segment to zero
    (exact: the waterfall is linear beyond the kink)."""
    idx = np.argmax(rec > 1e-9)
    if rec[idx] <= 1e-9:
        return None
    if idx == 0:
        return float(grid[0])
    if idx + 1 < len(rec) and rec[idx + 1] > rec[idx]:
        slope = (rec[idx + 1] - rec[idx]) / (grid[idx + 1] - grid[idx])
        return float(max(grid[idx] - rec[idx] / slope, 0.0))
    return _cross(grid, rec, 1e-9)


def explore(structure: CapitalStructure, ebitda: Optional[float],
            accrual_years: float = 0.0, quotes: Optional[list[float]] = None) -> dict:
    order = structure.priority_order()
    tmap = {t.name: t for t in structure.tranches}
    claims = {n: tmap[n].claim(accrual_years) for n in order}
    total_claim = sum(claims.values())
    if total_claim <= 0:
        return {"available": False, "note": "no claims in the structure"}

    grid = np.linspace(0.0, 1.5 * total_claim, _GRID_N)
    wf = run_waterfall(structure, grid, accrual_years=accrual_years)

    tranches = []
    for n in order:
        rec = wf[n]
        c = claims[n]
        pct = rec / c if c > 0 else np.zeros_like(rec)
        tranches.append({
            "tranche": n,
            "claim": round(c, 1),
            "recovery_pct": np.round(100 * pct, 2).tolist(),
            "ev_enters": _enters(grid, rec),
            "ev_covered": _covered(grid, pct),
        })

    out: dict = {
        "available": True,
        "ev_grid": np.round(grid, 1).tolist(),
        "tranches": tranches,
        "total_claim": round(total_claim, 1),
        "total_face": round(structure.total_face(), 1),
        "accrual_years": accrual_years,
        "derivation": "deterministic absolute-priority waterfall over an EV grid; "
                      "breakpoints linearly interpolated (waterfall is piecewise-linear in EV)",
    }

    if ebitda is not None and ebitda > 0:
        out["ebitda"] = ebitda
        out["multiple_grid"] = np.round(grid / ebitda, 3).tolist()
        # Moyer ch. 6 coverage-vs-multiple: EV/total, EV/senior, (EV − senior)/junior
        senior = sum(claims[n] for n in order if tmap[n].secured)
        junior = total_claim - senior
        m = np.arange(2.0, 10.01, 0.25)
        ev_m = m * ebitda
        cov = {"multiple": m.tolist(),
               "total": np.round(ev_m / total_claim, 3).tolist()}
        breakeven = {"total": round(total_claim / ebitda, 2)}
        if senior > 0:
            cov["senior"] = np.round(ev_m / senior, 3).tolist()
            breakeven["senior"] = round(senior / ebitda, 2)
        if junior > 0 and senior > 0:
            cov["junior"] = np.round(np.maximum(ev_m - senior, 0.0) / junior, 3).tolist()
            breakeven["junior"] = round(total_claim / ebitda, 2)
        out["coverage"] = cov
        out["breakeven_multiples"] = breakeven
        out["coverage_note"] = ("junior coverage = (EV − senior claims) ÷ junior claims — "
                                "distorted when the junior tranche is small (Moyer ch. 6)")
        # 'market has not repriced' (ch. 6): face debt exceeds the reference EV while the
        # quoted paper still trades near par
        if quotes:
            out["not_repriced"] = bool(structure.total_face() > 6.0 * ebitda
                                       and float(np.mean(quotes)) >= 90.0)
            out["not_repriced_note"] = ("total face > 6.0x EBITDA while mean matched quote ≥ 90 — "
                                        "the market has not repriced the debt to asset value")
    else:
        out["ebitda"] = ebitda
        out["multiple_grid"] = None

    return out
