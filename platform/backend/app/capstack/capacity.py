"""Credit-capacity machinery (Moyer ch. 6): the bank-style 5-year cash-sweep repayment
model, the leverage × growth sensitivity grid, the cycle-severity stress, and the
capacity-ratio chips (dual leverage + paired interest coverage). All deterministic,
seeded from the cached overview.

Repayment collapses between 3x and 5x: at 2.0x even 0% growth retires ~92% of a loan in
five years; at 5.0x almost nothing repays. Quoted EBITDA leverage understates true
leverage when capex is heavy — pair Debt/EBITDA with Debt/(EBITDA−CAPX).
"""
from __future__ import annotations

from typing import Optional

from ..edgar.facts import derived_value, fmt_money_millions, fmt_ratio
from ..schemas import CoverageChips

# Moyer's illustrative cycle: +5, −20, −10, +10, +10 % — scaled by a severity factor.
CYCLE = (0.05, -0.20, -0.10, 0.10, 0.10)
SEVERITIES = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75)


def sweep(debt0: float, ebitda0: float, rate: float, capex: float,
          growth: list[float]) -> dict:
    """Cash-sweep amortization: EBITDA_t grows by growth[t]; interest = rate × beginning
    debt; residual after capex sweeps principal. Taxes/working capital ignored (Moyer)."""
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


def heatmap(ebitda0: float, rate: float, capex_ratio: float,
            years: int = 5) -> dict:
    """% of debt retired over leverage 2.0–5.0x × growth 0–8% (Table 6-3 form)."""
    levs = [2.0 + 0.5 * i for i in range(7)]
    gs = [0.0, 0.02, 0.04, 0.06, 0.08]
    capex = capex_ratio * ebitda0
    cells = [[sweep(lv * ebitda0, ebitda0, rate, capex, [g] * years)["pct_retired"]
              for g in gs] for lv in levs]
    return {"leverage": levs, "growth": gs, "pct_retired": cells}


def severity_slices(debt0: float, ebitda0: float, rate: float, capex: float,
                    wall_by_year: Optional[list[dict]] = None) -> list[dict]:
    """The cycle vector scaled by each severity factor, with per-year flags:
    leverage_up_despite_paydown, and wall_breach when the maturity-wall face due in a year
    exceeds cumulative sweep capacity (substitute for unextracted amortization schedules)."""
    out = []
    for s in SEVERITIES:
        growth = [g * s for g in CYCLE]
        run = sweep(debt0, ebitda0, rate, capex, growth)
        cum_capacity = 0.0
        prev_lev = round(debt0 / ebitda0, 2) if ebitda0 > 0 else None
        flags = []
        for i, row in enumerate(run["rows"]):
            cum_capacity += row["available"]
            year_flags = []
            if (row["leverage"] is not None and prev_lev is not None
                    and row["leverage"] > prev_lev and row["debt_end"] < (debt0 if i == 0 else run["rows"][i - 1]["debt_end"])):
                year_flags.append("leverage_up_despite_paydown")
            if wall_by_year and i < len(wall_by_year):
                due = wall_by_year[i].get("face") or 0.0
                if due > cum_capacity:
                    year_flags.append("wall_breach")
            prev_lev = row["leverage"]
            flags.append(year_flags)
        out.append({"severity": s, "growth": [round(g, 3) for g in growth],
                    **run, "year_flags": flags})
    return out


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
    is unavailable/non-positive (card degrades to 'n.m.')."""
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


def build_capacity(ov: dict) -> dict:
    """The interactive card payload: base sweep + heatmap + severity slices."""
    inp = capacity_inputs(ov)
    if inp is None:
        return {"available": False,
                "note": "EBITDA or debt unavailable/non-positive — capacity model n.m. "
                        "(see Liquidity & runway for the cash-burner view)"}
    debt, e, capex, rate = inp["debt"], inp["ebitda"], inp["capex"], inp["rate"]
    wall = [{"year": b.get("year"), "face": (b.get("face") or 0) / 1e6}
            for b in sorted(ov.get("maturity_wall") or [], key=lambda b: b.get("year") or 0)]
    return {
        "available": True,
        "inputs": {**inp, "leverage": round(debt / e, 2), "capex_pct": round(100 * capex / e, 1)},
        "base_sweep": sweep(debt, e, rate, capex, [0.02] * 5),
        "heatmap": heatmap(e, rate, capex / e if e > 0 else 0.0),
        "severity": severity_slices(debt, e, rate, capex, wall),
        "derivation": "5-yr cash sweep: EBITDA×(1+g) − interest(rate × beginning debt) − "
                      "capex, residual amortizes principal (Moyer Tables 6-1..6-5; taxes "
                      "and working capital ignored; wall_breach substitutes for "
                      "unextracted amortization schedules)",
    }