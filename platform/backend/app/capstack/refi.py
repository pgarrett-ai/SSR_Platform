"""Refi-wall sequencing: can each maturity bucket be repaid internally, and if not, is the
market open to refinance it? Internal capacity is the cash-sweep model (capacity.sweep);
market access reads the issuer's bond marks (a yield of 40%+ means the refi market is
closed; a nearer quote's premium over longer pari paper proxies the market's refi
probability). Default probability is deliberately not an input: the only PD available is
risk-neutral Merton, which cannot be banded against real-world default rates.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Optional

from ..edgar.facts import derived_value, fmt_money_millions
from .capacity import capacity_inputs, sweep
from .quotes import _maturity_year, match_quotes

_MARKETS_CLOSED_YTM = 40.0   # a 40%+ yield means the refi market is closed
_GROWTH = 0.02               # flat sweep growth, matching the capacity base case

# facility_type values debt_xbrl.facility_type_of can emit that mean "bank paper"
BANK_TYPES = {"revolver", "term loan", "delayed-draw term loan", "credit facility",
              "bridge loan"}
# name fallback — load-bearing: AAL tags facility_type on none of its 14 rows.
# "Special Facility Revenue Bonds" and commercial paper correctly miss.
_BANK_NAME_RE = re.compile(r"revolv|term loan|credit facilit|\babl\b|\bddtl\b|bridge loan",
                           re.IGNORECASE)


def _is_bank(inst: dict) -> bool:
    """facility_type when tagged, name regex otherwise."""
    ft = inst.get("facility_type")
    if ft is not None:
        return ft in BANK_TYPES
    return bool(_BANK_NAME_RE.search(inst.get("instrument") or ""))


def sequence_walls(ladder: list[dict], liquidity_mm: float,
                   cum_internal: list[float], base_year: int) -> list[dict]:
    """Sequential funding, front to back: earlier walls consume resources first.
    resources_i = liquidity + cumulative sweep capacity by year i − Σ earlier faces;
    refi_need_i = max(face_i − max(resources_i, 0), 0). All $mm."""
    rows = []
    spent = 0.0
    for b in ladder:
        years_ahead = max(int(b["year"]) - base_year, 0)
        if years_ahead <= 0 or not cum_internal:
            internal = 0.0
        else:
            internal = cum_internal[min(years_ahead, len(cum_internal)) - 1]
        face = float(b["face_mm"])
        resources = liquidity_mm + internal - spent
        repayable = max(min(resources, face), 0.0)
        rows.append({**b, "internal_mm": round(internal, 1),
                     "resources_mm": round(resources, 1),
                     "repayable_mm": round(repayable, 1),
                     "refi_need_mm": round(face - repayable, 1)})
        spent += face
    return rows


def _cum_internal(inp: Optional[dict], n_years: int) -> tuple[list[float], Optional[str]]:
    """Cumulative sweep capacity ($mm) through each of the next n_years. 0-series +
    note when the capacity model is n.m. (negative EBITDA — the LCID persona)."""
    if inp is None:
        return [0.0] * n_years, ("EBITDA or debt unavailable/non-positive — internal "
                                 "repayment capacity set to $0 (see Liquidity & runway)")
    run = sweep(inp["debt"], inp["ebitda"], inp["rate"], inp["capex"],
                [_GROWTH] * n_years)
    out, cum = [], 0.0
    for row in run["rows"]:
        cum += row["available"]
        out.append(round(cum, 1))
    return out, None


def build_refi_wall(ov: dict, bonds: list[dict]) -> dict:
    """The refi-wall table: per bucket, internal repayability (sequential), the market
    overlay, and a verdict walked back to front (an unrefinanceable later wall poisons
    every earlier refinancing)."""
    wall = sorted(ov.get("maturity_wall") or [], key=lambda b: b.get("year") or 0)
    if not wall:
        return {"available": False,
                "note": "no maturity wall extracted — nothing to sequence"}

    base = dt.date.today()

    ladder = [{"year": int(b["year"]),
               "face_mm": round((b.get("face") or 0) / 1e6, 1),
               "instruments": list(b.get("instruments") or [])} for b in wall]

    notes: list[str] = []
    # None-guard: the stale AAL cache has no liquidity block at all
    liq_v = ((ov.get("liquidity") or {}).get("total_liquidity") or {}).get("value")
    liquidity_mm = (liq_v or 0.0) / 1e6   # $mm→raw-$ boundary kept here, in one place
    if liq_v is None:
        notes.append("no liquidity block in this cached overview — cash + undrawn "
                     "treated as $0 (re-run the pipeline to extract)")

    n_years = max(ladder[-1]["year"] - base.year, 1)
    cum_internal, cap_note = _cum_internal(capacity_inputs(ov), n_years)
    if cap_note:
        notes.append(cap_note)

    rows = sequence_walls(ladder, liquidity_mm, cum_internal, base.year)

    # market overlay: matched bond marks keyed to schedule rows
    quotes, _ = match_quotes(ov.get("debt_schedule") or [], bonds)
    sched_by_name = {(i.get("instrument") or ""): i
                     for i in ov.get("debt_schedule") or []}
    quoted = []
    for name, q in quotes.items():
        inst = sched_by_name.get(name) or {}
        y = _maturity_year(inst.get("maturity"))
        if y is None or q.get("last_price") is None:
            continue
        quoted.append({"instrument": name, "year": int(y),
                       "secured": inst.get("secured"),
                       "price": float(q["last_price"]), "ytm": q.get("last_yield")})

    for i, r in enumerate(rows):
        names = set(r["instruments"])
        bq = next((q for q in quoted if q["instrument"] in names), None)
        r["quote"] = ({"instrument": bq["instrument"], "price": bq["price"],
                       "ytm": bq["ytm"]} if bq else None)
        r["markets_closed"] = (bool(bq["ytm"] >= _MARKETS_CLOSED_YTM)
                               if bq and bq["ytm"] is not None else None)
        r["refi_prob_pct"] = None
        r["refi_prob_note"] = None
        if bq:
            # strictly-later pari passu anchor (pari = same secured bool), nearest year
            later = [q for q in quoted
                     if q["year"] > r["year"] and q["secured"] == bq["secured"]]
            anchor = min(later, key=lambda q: q["year"]) if later else None
            if anchor and anchor["price"] < 100.0:
                p = (bq["price"] - anchor["price"]) / (100.0 - anchor["price"])
                r["refi_prob_pct"] = round(100.0 * min(max(p, 0.0), 1.0), 1)
                r["refi_prob_note"] = (
                    f"(bucket quote {bq['price']:g} − longer pari "
                    f"{anchor['instrument'][:40]} @ {anchor['price']:g}) ÷ "
                    f"(100 − {anchor['price']:g}) — the nearer paper's premium over "
                    "longer pari paper")
            elif not later:
                r["refi_prob_note"] = ("no strictly-longer pari passu quote — "
                                       "refi probability not anchorable")

    # verdicts, back to front: an unrefinanceable later wall poisons every earlier
    # refinancing (the new lender would have to survive it)
    unref_later = False
    n = len(rows)
    for i in range(n - 1, -1, -1):
        r = rows[i]
        closed = bool(r.get("markets_closed"))
        if i == n - 1:
            unref = r["refi_need_mm"] > 0 and closed
        else:
            unref = closed or unref_later
        if unref:
            r["verdict"] = "unrefinanceable"
            unref_later = True
        elif r["refi_need_mm"] > 0:
            r["verdict"] = "refi_needed"
        else:
            r["verdict"] = "fundable"

    # annotations (front to back): term-structure seniority + bank-facility renewal bias
    for i, r in enumerate(rows):
        ann: list[str] = []
        if i > 0 and rows[i - 1]["verdict"] == "fundable":
            ann.append("nearer wall self-funding — new money at this wall can be "
                       "structured to mature inside it (term-structure seniority)")
        if any(_is_bank(sched_by_name.get(nm) or {"instrument": nm})
               for nm in r["instruments"]):
            ann.append("includes a bank facility — relationship lenders typically "
                       "extend/renew rather than demand a takeout (holder-type bias "
                       "stated, not modeled)")
        if r["verdict"] == "unrefinanceable" and r["refi_need_mm"] <= 0:
            ann.append("repayable internally — but this market would not refinance it")
        r["annotations"] = ann

    # displayed dollars → derived CitedValues
    def m(v: float) -> str:   # $mm -> "-$123M"
        return fmt_money_millions(v * 1e6)

    out_rows = []
    for r in rows:
        insts = ", ".join(r["instruments"])[:120]
        face = derived_value(r["face_mm"], f"Σ face due {r['year']}: {insts}",
                             m(r["face_mm"])).model_dump()
        repay = derived_value(
            r["repayable_mm"],
            f"min(face {m(r['face_mm'])}, max(liquidity {m(liquidity_mm)} + "
            f"cumulative sweep {m(r['internal_mm'])} − earlier walls, 0)) — "
            f"flat {100 * _GROWTH:.0f}% growth sweep",
            m(r["repayable_mm"])).model_dump()
        need = derived_value(
            r["refi_need_mm"],
            f"face {m(r['face_mm'])} − repayable internally {m(r['repayable_mm'])}",
            m(r["refi_need_mm"])).model_dump()
        out_rows.append({
            "year": r["year"], "instruments": r["instruments"],
            "face": face, "repayable": repay, "refi_need": need,
            "quote": r["quote"], "markets_closed": r["markets_closed"],
            "refi_prob_pct": r["refi_prob_pct"], "refi_prob_note": r["refi_prob_note"],
            "verdict": r["verdict"], "annotations": r["annotations"],
        })

    return {
        "available": True,
        "rows": out_rows,
        "liquidity_mm": round(liquidity_mm, 1),
        "notes": notes,
        "derivation": "sequential funding: resources_i = liquidity + cumulative sweep "
                      "capacity (flat 2% growth) − Σ earlier wall faces; refi need = "
                      "max(face − resources, 0); markets closed at YTM ≥ 40%; "
                      "unrefinanceable = markets closed here or at a later wall; "
                      "range-spread maturities amortize evenly across the "
                      "wall — bucket faces inherit that convention",
    }
