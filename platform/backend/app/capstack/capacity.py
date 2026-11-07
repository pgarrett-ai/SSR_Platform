"""Credit-capacity machinery (Moyer, Distressed Debt Analysis, ch. 6): the bank-style
cash-sweep repayment model the refi wall sequences maturities with, and the capacity-ratio
chips (dual leverage + paired interest coverage). All deterministic, seeded from the cached
overview.

Quoted EBITDA leverage understates true leverage when capex is heavy — pair Debt/EBITDA
with Debt/(EBITDA−CAPX).
"""
from __future__ import annotations

from typing import Optional

from ..edgar.facts import derived_value, fmt_money_millions, fmt_ratio
from ..schemas import CoverageChips


def sweep(debt0: float, ebitda0: float, rate: float, capex: float,
          growth: list[float]) -> dict:
    """Cash-sweep amortization: EBITDA_t grows by growth[t]; interest = rate × beginning
    debt; residual after capex sweeps principal. Taxes and working capital are ignored."""
    rows = []
    debt, e = debt0, ebitda0
    for g in growth:
        e = e * (1.0 + g)
        interest = rate * debt
        avail = max(e - interest - capex, 0.0)
        amort = min(avail, debt)
        debt_end = debt - amort
        rows.append({
            "ebitda": round(e, 1), "interest": round(interest, 1),
            "available": round(avail, 1), "debt_end": round(debt_end, 1),
            "leverage": round(debt_end / e, 2) if e > 0 else None,
            "coverage": round(e / interest, 2) if interest > 0 else None,
        })
        debt = debt_end
    pct = 100.0 * (debt0 - debt) / debt0 if debt0 > 0 else None
    return {"rows": rows, "pct_retired": round(pct, 1) if pct is not None else None}


def coverage_chips(debt: Optional[float], ebitda: Optional[float],
                   capex: Optional[float], interest: Optional[float]) -> Optional[CoverageChips]:
    """The paired capacity ratios as derived CitedValues; n.m. (None value) guards mirror
    the bridge's leverage handling at non-positive denominators."""
    if debt is None or ebitda is None:
        return None

    def ratio(num, den, formula):
        if num is None or den is None or den <= 0:
            return derived_value(None, formula, None, note="n.m. — non-positive denominator")
        v = round(num / den, 2)
        return derived_value(v, formula, fmt_ratio(v))

    ec = ebitda - capex if capex is not None else None
    return CoverageChips(
        debt_ebitda=ratio(debt, ebitda,
                          f"debt {fmt_money_millions(debt)} ÷ EBITDA {fmt_money_millions(ebitda)}"),
        debt_ebitda_capex=ratio(debt, ec,
                                f"debt ÷ (EBITDA − capex {fmt_money_millions(capex)})"),
        ebitda_interest=ratio(ebitda, interest,
                              f"EBITDA ÷ interest {fmt_money_millions(interest)}"),
        ebitda_capex_interest=ratio(ec, interest, "(EBITDA − capex) ÷ interest"),
        capex_pct_ebitda=(round(100 * capex / ebitda, 1)
                          if capex is not None and ebitda and ebitda > 0 else None),
    )


def capacity_inputs(ov: dict) -> Optional[dict]:
    """Pull the sweep-model inputs from an overview dict ($mm). None when EBITDA or debt
    is unavailable/non-positive (the refi wall then credits no internal repayment)."""
    def latest(key):
        for row in reversed(ov.get("forensic_table") or []):
            cv = row.get(key)
            if cv and cv.get("value") is not None:
                return float(cv["value"])
        return None

    bridge = ov.get("economic_debt_bridge") or {}
    debt = (bridge.get("reported_debt") or {}).get("value")
    if debt is None:
        debt = latest("total_debt")
    ebitda = (bridge.get("ebitda") or {}).get("value") or latest("ebitda")
    capex = latest("capex")

    # weighted-average rate over schedule rows that carry one (adapter semantics)
    from ..fulcrum.adapter import _tranche_coupon
    tot = wsum = 0.0
    unrated = 0
    for inst in ov.get("debt_schedule") or []:
        cv = inst.get("outstanding") or inst.get("principal")
        face = (cv or {}).get("value")
        if not face or face <= 0:
            continue
        r = _tranche_coupon(inst)
        if r > 0:
            tot += face
            wsum += face * r
        else:
            unrated += 1
    rate = wsum / tot if tot > 0 else None

    if not ebitda or ebitda <= 0 or not debt or debt <= 0:
        return None
    return {
        "debt": debt / 1e6, "ebitda": ebitda / 1e6,
        "capex": (capex or 0.0) / 1e6,
        "rate": rate if rate is not None else 0.08,
        "rate_note": (f"weighted-average tagged rate over {fmt_money_millions(tot)} of debt"
                      + (f"; {unrated} instrument(s) without a rate excluded" if unrated else ""))
                     if rate is not None else "no tagged rates — 8% assumed",
        "capex_note": "latest FY capex from the forensic table (run-rate, not a 3y average)",
    }