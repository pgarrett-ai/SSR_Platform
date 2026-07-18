"""Refi-wall sequencing (Moyer ch. 6/10): can each maturity bucket be repaid internally,
and if not, will anyone refinance it? Internal capacity is the ch. 6 cash-sweep model;
the default leg is a Merton PD term structure re-solved from the cached hazard inputs
(risk-neutral, single default point D = total debt); the market leg reads the TRACE
drop-file (YTM ≥ 40% = markets closed — book verbatim; a nearer quote's premium over
longer pari paper proxies the market's refi probability). Calculator, not monitor —
the hazard cache is used at any age, and its file/as-of are surfaced.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Optional

from ..core.cache import safe_ticker
from ..core.config import CACHE_DIR, get_settings
from ..edgar.facts import derived_value
from ..hazard.merton import merton
from ..hazard.score import implied_rating
from .capacity import capacity_inputs, sweep
from .quotes import _maturity_year, match_quotes
from .triage import _is_bank

_MARKETS_CLOSED_YTM = 40.0   # Moyer ch. 10: a 40%+ yield means the refi market is closed
_GROWTH = 0.02               # flat sweep growth — matches the capacity card's base case

_METHODOLOGY = ("risk-neutral Merton PD term structure re-solved from the cached hazard "
                "inputs (E = market cap, σ_E = equity vol, single default point D = "
                "total debt); conditional PD over an interval = "
                "(PD(tᵢ) − PD(tᵢ₋₁)) ÷ (1 − PD(tᵢ₋₁)); 'default looks likely' when the "
                "nearest S&P long-run band by log-distance is CCC/C (≈9.7% cutoff — "
                "platform-chosen)")


def hazard_inputs(ticker: str) -> Optional[dict]:
    """Merton inputs from the newest cached hazard payload for the ticker — glob
    {TICKER}_*y.json and take the newest (NOT years-keyed: only *_10y.json typically
    exists). Any age (calculator, not monitor); file + as_of surfaced in the payload.
    None when no usable cache."""
    try:
        t = safe_ticker(ticker)   # trust boundary: keep request-derived ticker out of the glob
    except ValueError:
        return None
    files = sorted(CACHE_DIR.glob(f"hazard/{t}_*y.json"),
                   key=lambda p: p.stat().st_mtime)
    for p in reversed(files):
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
            data = blob.get("data") or {}
            market = data.get("market") or {}
            ft = data.get("features_timeline") or []
            E = market.get("market_cap")
            sigma_E = market.get("equity_vol")
            D = (ft[-1] or {}).get("total_debt") if ft else None
            if E and sigma_E and D and E > 0 and sigma_E > 0 and D > 0:
                return {"E": float(E), "sigma_E": float(sigma_E), "D": float(D),
                        "r": get_settings().risk_free_rate,
                        "as_of": blob.get("as_of"), "file": p.name}
        except Exception:
            continue
    return None


def conditional_from_cum(cum: list[float]) -> list[float]:
    """Interval PDs conditional on surviving the prior horizon:
    cᵢ = (Pᵢ − Pᵢ₋₁) ÷ (1 − Pᵢ₋₁), floored at 0 (a risk-neutral cum PD can dip at
    long horizons when drift dominates)."""
    out, prev = [], 0.0
    for p in cum:
        out.append(max((p - prev) / (1.0 - prev), 0.0) if prev < 1.0 else 0.0)
        prev = p
    return out


def conditional_pds(E: float, sigma_E: float, D: float, r: float,
                    horizons: tuple[float, ...]) -> Optional[dict]:
    """One merton() re-solve (reproduces the pipeline's cached PDs exactly) →
    cumulative + conditional PD per horizon."""
    res = merton(E=E, sigma_E=sigma_E, D=D, r=r, horizons=tuple(horizons))
    if res is None:
        return None
    cum = [float(res.pd_by_horizon[h]) for h in horizons]
    return {"cum": cum, "conditional": conditional_from_cum(cum),
            "converged": res.converged}


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


def build_refi_wall(ov: dict, bonds: list[dict], hz: Optional[dict]) -> dict:
    """The refi-wall table: per bucket, internal repayability (sequential), the
    conditional-PD leg to the next wall, the market overlay, and a verdict walked back
    to front (an unrefinanceable later wall poisons every earlier refinancing)."""
    wall = sorted(ov.get("maturity_wall") or [], key=lambda b: b.get("year") or 0)
    if not wall:
        return {"available": False,
                "note": "no maturity wall extracted — nothing to sequence"}

    base = dt.date.today()
    if hz and hz.get("as_of"):
        try:
            base = dt.date.fromisoformat(str(hz["as_of"])[:10])
        except ValueError:
            pass

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

    # PD leg: one merton re-solve at year-end horizons from the hazard as_of
    horizons = tuple(max((dt.date(r["year"], 12, 31) - base).days / 365.25, 1.0 / 12.0)
                     for r in rows)
    pds = (conditional_pds(hz["E"], hz["sigma_E"], hz["D"], hz["r"], horizons)
           if hz is not None else None)

    # market overlay: matched drop-file quotes keyed to schedule rows
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
                    "longer pari paper (Moyer ch. 10)")
            elif not later:
                r["refi_prob_note"] = ("no strictly-longer pari passu quote — "
                                       "refi probability not anchorable")

    # verdicts, back to front: an unrefinanceable later wall poisons every earlier
    # refinancing (the new lender would have to survive it)
    unref_later = False
    n = len(rows)
    for i in range(n - 1, -1, -1):
        r = rows[i]
        # displayed conditional PD = the interval a refi lender at this wall bears —
        # to the NEXT wall (the last wall shows the conditional into it)
        if pds is not None:
            j = min(i + 1, n - 1)
            r["cond_pd"] = pds["conditional"][j]
            r["cum_pd"] = pds["cum"][i]
            r["band"] = implied_rating(r["cond_pd"]) if r["cond_pd"] > 0 else None
            pd_leg = r["band"] == "CCC/C"
        else:
            r["cond_pd"] = r["cum_pd"] = r["band"] = None
            # no hazard → next wall's own refi need proxies the default leg
            pd_leg = (i < n - 1) and rows[i + 1]["refi_need_mm"] > 0
        own_flag = pd_leg or bool(r.get("markets_closed"))
        if i == n - 1:
            unref = r["refi_need_mm"] > 0 and own_flag
        else:
            unref = own_flag or unref_later
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
                       "structured to mature inside it (term-structure seniority, "
                       "Moyer ch. 10)")
        if any(_is_bank(sched_by_name.get(nm) or {"instrument": nm})
               for nm in r["instruments"]):
            ann.append("includes a bank facility — relationship lenders typically "
                       "extend/renew rather than demand a takeout (holder-type bias "
                       "stated, not modeled)")
        if r["verdict"] == "unrefinanceable" and r["refi_need_mm"] <= 0:
            ann.append("repayable internally — but this market would not refinance it")
        r["annotations"] = ann

    # displayed dollars → derived CitedValues; PDs stay raw floats + methodology
    out_rows = []
    for r in rows:
        insts = ", ".join(r["instruments"])[:120]
        face = derived_value(r["face_mm"], f"Σ face due {r['year']}: {insts}",
                             f"${r['face_mm']:,.0f}M").model_dump()
        repay = derived_value(
            r["repayable_mm"],
            f"min(face ${r['face_mm']:,.0f}M, max(liquidity ${liquidity_mm:,.0f}M + "
            f"cumulative sweep ${r['internal_mm']:,.0f}M − earlier walls, 0)) — "
            f"flat {100 * _GROWTH:.0f}% growth sweep",
            f"${r['repayable_mm']:,.0f}M").model_dump()
        need = derived_value(
            r["refi_need_mm"],
            f"face ${r['face_mm']:,.0f}M − repayable internally "
            f"${r['repayable_mm']:,.0f}M",
            f"${r['refi_need_mm']:,.0f}M").model_dump()
        out_rows.append({
            "year": r["year"], "instruments": r["instruments"],
            "face": face, "repayable": repay, "refi_need": need,
            "cond_pd": r["cond_pd"], "cum_pd": r["cum_pd"], "band": r["band"],
            "quote": r["quote"], "markets_closed": r["markets_closed"],
            "refi_prob_pct": r["refi_prob_pct"], "refi_prob_note": r["refi_prob_note"],
            "verdict": r["verdict"], "annotations": r["annotations"],
        })

    return {
        "available": True,
        "rows": out_rows,
        "liquidity_mm": round(liquidity_mm, 1),
        "hazard": ({"file": hz["file"], "as_of": hz["as_of"],
                    "converged": pds["converged"] if pds else None,
                    "methodology": _METHODOLOGY} if hz else None),
        "hazard_note": (None if hz else
                        "no hazard cache — run Default Risk once to populate the PD "
                        "leg; until then the next wall's refi need proxies it"),
        "notes": notes,
        "derivation": "sequential funding: resources_i = liquidity + cumulative sweep "
                      "capacity (flat 2% growth) − Σ earlier wall faces; refi need = "
                      "max(face − resources, 0); markets closed at YTM ≥ 40% (Moyer "
                      "ch. 10); range-spread maturities amortize evenly across the "
                      "wall — bucket faces inherit that convention",
    }
