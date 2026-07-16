"""Match TRACE drop-file bond quotes to debt-schedule instruments (deterministic).

The drop file (hazard/data/bond_quotes.json, manual browser-scrape refresh) has no
instrument names, and the XBRL debt schedule has no CUSIPs, so matching keys on
(coupon, maturity year). An ambiguous key — two instruments or two bonds sharing
coupon+year — matches nothing rather than guessing; the miss is reported in `notes`.
"""
from __future__ import annotations

import re
from typing import Optional

_YEAR = re.compile(r"(19|20)\d{2}")


def _maturity_year(maturity: Optional[str]) -> Optional[str]:
    if not maturity:
        return None
    hits = [m.group(0) for m in _YEAR.finditer(maturity)]
    if not hits:
        return None
    if len(set(hits)) > 1:
        return None   # range-spread instruments ("2026 to 2038") have no single year
    return hits[0]


def _key(coupon: Optional[float], year: Optional[str]) -> Optional[tuple[float, str]]:
    if coupon is None or year is None:
        return None
    return (round(float(coupon), 2), year)


def match_quotes(debt_schedule: list[dict], bonds: list[dict]) -> tuple[dict[str, dict], list[str]]:
    """{instrument name: quote dict} for unambiguous (coupon, maturity-year) matches.
    Returns (matches, notes)."""
    inst_by_key: dict[tuple, list[str]] = {}
    for inst in debt_schedule or []:
        k = _key(inst.get("coupon_pct"), _maturity_year(inst.get("maturity")))
        if k is not None:
            inst_by_key.setdefault(k, []).append(inst.get("instrument") or "")

    bond_by_key: dict[tuple, list[dict]] = {}
    for b in bonds or []:
        k = _key(b.get("coupon"), _maturity_year(b.get("maturity")))
        if k is not None:
            bond_by_key.setdefault(k, []).append(b)

    matches: dict[str, dict] = {}
    notes: list[str] = []
    for k, insts in inst_by_key.items():
        bs = bond_by_key.get(k) or []
        if not bs:
            continue
        if len(insts) > 1 or len(bs) > 1:
            notes.append(f"ambiguous quote match on coupon {k[0]}% / {k[1]} — left unquoted")
            continue
        matches[insts[0]] = bs[0]
    return matches, notes


# Treasury tenor anchors for the coarse curve: 3M bill, 10Y, 30Y (app.rates SERIES).
_TENORS = [("DTB3", 0.25), ("DGS10", 10.0), ("DGS30", 30.0)]


def spread_bps(bond: dict, treasuries: dict[str, float],
               asof_year: Optional[int] = None) -> Optional[float]:
    """last_yield minus a treasury interpolated by years-to-maturity from the 3 stored
    points. Coarse 3-point curve — a screening number, not a pricing one."""
    y = bond.get("last_yield")
    year = _maturity_year(bond.get("maturity"))
    if y is None or year is None:
        return None
    pts = [(t, treasuries.get(s)) for s, t in _TENORS]
    pts = [(t, v) for t, v in pts if v is not None]
    if not pts:
        return None
    import datetime as dt
    base = asof_year or dt.date.today().year
    ttm = max(float(year) - base, 0.25)
    # piecewise-linear interpolation, clamped at the ends
    pts.sort()
    lo_t, lo_v = pts[0]
    hi_t, hi_v = pts[-1]
    if ttm <= lo_t:
        tsy = lo_v
    elif ttm >= hi_t:
        tsy = hi_v
    else:
        for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
            if t0 <= ttm <= t1:
                tsy = v0 + (v1 - v0) * (ttm - t0) / (t1 - t0)
                break
    return round((float(y) - tsy) * 100.0, 0)
