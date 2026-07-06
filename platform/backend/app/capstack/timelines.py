"""Phase 4.6: leverage timeline, maturity wall, and the "what changed" card.

Quarterly cadence (4.6b): `build_quarterly_series` reads 10-Q XBRL and computes TTM flows,
so the timeline and the change card move quarter-over-quarter (TTM EBITDA/FCF, point-in-time
debt/cash) — the industry-standard view. The annual FY-vs-FY versions remain as the fallback
when an issuer's quarterly facts are unavailable.
"""
from __future__ import annotations

import re

from ..edgar.facts import QuarterFacts
from ..schemas import (
    ChangeItem,
    DebtInstrument,
    ForensicTableRow,
    LeverageTimelinePoint,
    MaturityBucket,
)


def _v(cv) -> float | None:
    return cv.value if cv is not None else None


def leverage_timeline(rows: list[ForensicTableRow]) -> list[LeverageTimelinePoint]:
    """Reported debt / EBITDA per fiscal year, from the forensic table."""
    out = []
    for r in rows:
        debt, ebd = _v(r.total_debt), _v(r.ebitda)
        out.append(LeverageTimelinePoint(
            fiscal_year=r.fiscal_year, reported_debt=debt, ebitda=ebd,
            leverage=debt / ebd if debt and ebd and ebd > 0 else None,
        ))
    return out


_YEAR = re.compile(r"\b(20\d\d)\b")


def maturity_wall(instruments: list[DebtInstrument]) -> list[MaturityBucket]:
    """Face due per calendar year, parsed from the footnote maturity strings.
    'February 2028' → 2028; 'from 2026 to 2038' → face spread evenly across the range."""
    buckets: dict[int, MaturityBucket] = {}

    def add(year: int, face: float, name: str) -> None:
        b = buckets.setdefault(year, MaturityBucket(year=year, face=0.0))
        b.face += face
        if name not in b.instruments:
            b.instruments.append(name)

    for inst in instruments:
        cv = inst.outstanding or inst.principal
        face = _v(cv)
        if not face or face <= 0:
            continue
        years = [int(y) for y in _YEAR.findall(inst.maturity or "")]
        if not years:
            continue
        lo, hi = min(years), max(years)
        span = list(range(lo, hi + 1))
        for y in span:                      # single year → span of 1
            add(y, face / len(span), inst.instrument)
    return [buckets[y] for y in sorted(buckets)]


# metric key → (label, unit, higher_is_worse)
_CHANGE_METRICS = {
    "total_debt": ("Total reported debt", "USD", True),
    "cash": ("Cash & equivalents", "USD", False),
    "ebitda": ("EBITDA (proxy)", "USD", False),
    "free_cash_flow": ("Free cash flow", "USD", False),
    "revenue": ("Revenue", "USD", False),
}


def _diff(pairs: list[tuple[str, str, bool, float | None, float | None]],
          threshold_pct: float, **labels) -> list[ChangeItem]:
    """Shared change-list core: (metric, unit, worse_up, prior, latest) → sorted ChangeItems."""
    out: list[ChangeItem] = []
    for metric, unit, worse_up, p, l in pairs:
        if p is None or l is None or p == 0:
            continue
        delta = 100.0 * (l - p) / abs(p)
        if abs(delta) < threshold_pct:
            continue
        out.append(ChangeItem(
            metric=metric, unit=unit, prior=p, latest=l, delta_pct=round(delta, 1),
            direction=("worse" if (delta > 0) == worse_up else "better"), **labels,
        ))
    out.sort(key=lambda c: abs(c.delta_pct or 0), reverse=True)   # biggest movers first
    return out


def what_changed(rows: list[ForensicTableRow], threshold_pct: float = 2.0) -> list[ChangeItem]:
    """Latest FY vs prior FY on the headline credit metrics, plus derived leverage.
    Moves under threshold_pct are 'flat' and dropped — the card shows what moved."""
    if len(rows) < 2:
        return []
    prior, latest = rows[-2], rows[-1]
    pairs = [(label, unit, worse_up, _v(getattr(prior, key)), _v(getattr(latest, key)))
             for key, (label, unit, worse_up) in _CHANGE_METRICS.items()]
    lev = [(_v(r.total_debt) / _v(r.ebitda)) if _v(r.total_debt) and _v(r.ebitda) and _v(r.ebitda) > 0
           else None for r in (prior, latest)]
    pairs.append(("Leverage (debt/EBITDA)", "x", True, lev[0], lev[1]))
    return _diff(pairs, threshold_pct,
                 prior_fy=prior.fiscal_year, latest_fy=latest.fiscal_year)


# ---------------------------------------------------------------------------
# Quarterly (TTM) versions — same outputs, quarter-over-quarter
# ---------------------------------------------------------------------------


def _q_debt(q: QuarterFacts) -> float | None:
    parts = [getattr(q.get(k), "numeric_value", None)
             for k in ("lt_debt_noncurrent", "lt_debt_current", "short_term_debt")]
    present = [p for p in parts if p is not None]
    return sum(present) if present else None


def _q_ebitda(q: QuarterFacts) -> float | None:
    oi, da = q.ttm.get("operating_income"), q.ttm.get("d_and_a")
    return oi + (da or 0.0) if oi is not None else None


def quarterly_leverage_timeline(quarters: list[QuarterFacts]) -> list[LeverageTimelinePoint]:
    """Point-in-time debt / TTM EBITDA per quarter end."""
    out = []
    for q in quarters:
        debt, ebd = _q_debt(q), _q_ebitda(q)
        out.append(LeverageTimelinePoint(
            fiscal_year=q.period_end.year, label=q.label, period_end=q.period_end.isoformat(),
            reported_debt=debt, ebitda=ebd,
            leverage=debt / ebd if debt and ebd and ebd > 0 else None,
        ))
    return out


def what_changed_quarterly(quarters: list[QuarterFacts],
                           threshold_pct: float = 2.0) -> list[ChangeItem]:
    """Latest quarter vs prior quarter: point-in-time debt/cash, TTM flows, TTM leverage."""
    if len(quarters) < 2:
        return []
    prior, latest = quarters[-2], quarters[-1]

    def vals(q: QuarterFacts):
        ocf, capex = q.ttm.get("operating_cash_flow"), q.ttm.get("capex")
        return {
            "Total reported debt": _q_debt(q),
            "Cash & equivalents": getattr(q.get("cash"), "numeric_value", None),
            "EBITDA (TTM)": _q_ebitda(q),
            "Free cash flow (TTM)": ocf - (capex or 0.0) if ocf is not None else None,
            "Revenue (TTM)": q.ttm.get("revenue"),
        }

    worse_up = {"Total reported debt": True}
    p, l = vals(prior), vals(latest)
    pairs = [(m, "USD", worse_up.get(m, False), p[m], l[m]) for m in p]
    lev = [(_q_debt(q) / _q_ebitda(q)) if _q_debt(q) and _q_ebitda(q) and _q_ebitda(q) > 0
           else None for q in (prior, latest)]
    pairs.append(("Leverage (debt/EBITDA TTM)", "x", True, lev[0], lev[1]))
    return _diff(pairs, threshold_pct, prior_label=prior.label, latest_label=latest.label,
                 prior_fy=prior.period_end.year, latest_fy=latest.period_end.year)
