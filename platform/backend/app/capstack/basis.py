"""Effective cost basis & trading-flat mechanics (Moyer ch. 5): what a distressed buyer
actually pays (quote + accrued, unless the paper trades flat) and what claim that money
buys (accreted value + accrued — unamortized OID is not a claim, §502(b)(2)).

Rides the /capital/ladder payload — per matched drop-file quote only, deterministic.
30/360 US day count; coupon dates anchor on the quote's full ISO maturity (fallback:
the schedule's maturity month, assumption stated). Settle = the quote's as-of date.
The flat toggle and coupons-before-restructure arithmetic are client-side over the
returned coupon-date list.
"""
from __future__ import annotations

import calendar
import datetime as dt
from typing import Optional

from ..edgar.facts import derived_value
from .liquidity import _parse_maturity
from .quotes import match_quotes


def days_30_360(d1: dt.date, d2: dt.date) -> int:
    """US 30/360 bond-basis day count — the accrued-interest convention for notes."""
    dd1 = min(d1.day, 30)
    dd2 = min(d2.day, 30) if dd1 == 30 else d2.day
    return (d2.year - d1.year) * 360 + (d2.month - d1.month) * 30 + (dd2 - dd1)


def _add_months(d: dt.date, n: int) -> dt.date:
    m = d.month - 1 + n
    y, mo = d.year + m // 12, m % 12 + 1
    return dt.date(y, mo, min(d.day, calendar.monthrange(y, mo)[1]))


def coupon_schedule(maturity_iso: str, per_year: int, settle: dt.date
                    ) -> tuple[dt.date, list[dt.date]]:
    """(last coupon strictly before settle, remaining coupon dates after settle through
    maturity). Dates anchor on the maturity month/day, stepping back 12/per_year months.
    Strictly-before keeps a full period accrued on a coupon date — the distressed read
    (a due-today coupon on stressed paper is accrued, not paid)."""
    m = dt.date.fromisoformat(str(maturity_iso)[:10])
    step = 12 // per_year
    dates = [m]
    while dates[-1] >= settle:
        dates.append(_add_months(dates[-1], -step))
    return dates[-1], sorted(d for d in dates if d > settle)


def _iso_date(s: Optional[str]) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def build_basis(ov: dict, bonds: list[dict], settle: Optional[dt.date] = None) -> dict:
    """Per-quote basis rows for the ladder payload. `settle` overrides the per-quote
    as-of date (tests); default is each quote's own as-of, else today."""
    quotes, _ = match_quotes(ov.get("debt_schedule") or [], bonds)
    by_name = {(i.get("instrument") or ""): i for i in ov.get("debt_schedule") or []}
    # instruments whose next coupon the event calendar flagged at-risk -> flat hint
    at_risk = {e.get("instrument") for e in ov.get("liquidity_events") or []
               if e.get("kind") == "coupon" and "coupon_at_risk" in (e.get("flags") or [])}
    # default client restructure date: earliest maturity on the 24-mo event calendar
    default_restructure = min(
        (e.get("date") for e in ov.get("liquidity_events") or []
         if e.get("kind") == "maturity" and e.get("date")), default=None)

    rows: list[dict] = []
    for name, q in sorted(quotes.items()):
        inst = by_name.get(name) or {}
        coupon = inst.get("coupon_pct")
        price = q.get("last_price")
        if coupon is None or price is None:
            continue
        # semiannual for notes, quarterly otherwise (liquidity.py convention, inline)
        is_notes = ((inst.get("facility_type") or "").lower() == "notes"
                    or "note" in name.lower())
        per_year = 2 if is_notes else 4

        assumption = None
        maturity_iso = q.get("maturity")
        if _iso_date(maturity_iso) is None:      # no full ISO on the quote — fall back
            year, month = _parse_maturity(inst.get("maturity"))
            if year is None:
                continue
            maturity_iso = dt.date(year, month or 12, 15).isoformat()
            assumption = "coupon dates anchored on schedule maturity month (day-15 assumed)"

        row_settle = settle or _iso_date(q.get("as_of")) or dt.date.today()
        last_cpn, future = coupon_schedule(maturity_iso, per_year, row_settle)
        days = days_30_360(last_cpn, row_settle)
        acc = round(float(coupon) * days / 360.0, 3)
        accrued = derived_value(
            acc,
            f"{float(coupon):g}% coupon × {days}/360 (30/360; last coupon "
            f"{last_cpn.isoformat()}, settle {row_settle.isoformat()}, "
            f"{'semiannual' if per_year == 2 else 'quarterly'})",
            f"{acc:.3f}", note=assumption)
        accrued.unit = "per 100 face"

        # claim per 100 face: accreted value + accrued (OID disallowed, §502(b)(2))
        face_v = ((inst.get("face_amount") or {}).get("value"))
        acc_v = ((inst.get("outstanding") or inst.get("principal") or {}).get("value"))
        if face_v and acc_v:
            oid = face_v > acc_v * 1.005
            claim_val = round(100.0 * acc_v / face_v + acc, 2)
            claim = derived_value(
                claim_val,
                f"100 × accreted {acc_v / 1e6:,.1f} ÷ face {face_v / 1e6:,.1f} + accrued "
                f"{acc:.3f} — unamortized OID disallowed (§502(b)(2), Moyer ch. 5)",
                f"{claim_val:.2f}")
            pct_of_accreted = round(price * face_v / acc_v, 1)
        else:
            oid = False
            claim_val = round(100.0 + acc, 2)
            claim = derived_value(
                claim_val, f"100 + accrued {acc:.3f} (no OID fields extracted — face = "
                           "carrying assumed)", f"{claim_val:.2f}")
            pct_of_accreted = price
        claim.unit = "per 100 face"

        basis = round(price + acc, 2)   # not-flat default; client recomputes on toggle
        rows.append({
            "instrument": name[:80],
            "quote": price,
            "last_yield": q.get("last_yield"),
            "as_of": q.get("as_of"),
            "settle": row_settle.isoformat(),
            "coupon_pct": float(coupon),
            "per_year": per_year,
            "accrued": accrued.model_dump(),
            "basis": basis,
            "claim_per_100": claim.model_dump(),
            "cost_pct_of_claim": round(100.0 * basis / claim_val, 1) if claim_val > 0 else None,
            "pct_of_accreted": pct_of_accreted,
            "oid": oid,
            "flat_hint": bool(name in at_risk or float(coupon) == 0.0),
            "coupons": [{"date": d.isoformat(), "amount": round(float(coupon) / per_year, 3)}
                        for d in future],
        })

    return {
        "rows": rows,
        "default_restructure": default_restructure,
        "note": ("No matched quotes — TRACE drop-file empty or unmatched."
                 if not rows else None),
        "hint": "use effective basis as the IRR-matrix entry",
        "derivation": "effective basis = quote + accrued (quote only when trading flat); "
                      "claim/100 = accreted value + accrued; cash-at-risk = basis − "
                      "coupons received before the restructuring date (Moyer ch. 5)",
    }
