"""Creation-multiple ladder: what multiple of EBITDA are you 'creating' the company at
through each class of the capital structure — at face and at market (Moyer ch. 1/10/11:
the Magellan computation; market value of the stack through a class ÷ LTM EBITDA).

Deterministic. Faces come from the same adapter the Recovery page uses (so class ordering
is the structural priority order, never re-derived); market values use the TRACE drop-file
quotes where an instrument matched, face otherwise (flagged `unquoted`).
All dollar figures in $mm.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from ..edgar.facts import derived_value, fmt_ratio
from ..fulcrum.adapter import overview_to_structure
from ..fulcrum.waterfall import run_waterfall
from .quotes import match_quotes

# Fulcrum-marker EV assumption; matches SimConfig.base_multiple's default.
_FULCRUM_MULTIPLE = 6.0


def _class_label(secured: bool, preferred: bool, lien_rank: int, entity: str,
                 multi_entity: bool) -> str:
    if preferred:
        base = "Preferred"
    elif secured:
        base = {1: "1st lien", 2: "2nd lien", 3: "3rd lien"}.get(lien_rank, f"Lien {lien_rank}")
    else:
        base = "Unsecured"
    return f"{base} @ {entity}" if multi_entity else base


def _ebitda_variants(ov: dict) -> dict[str, Optional[float]]:
    """$mm. 'ltm' via the same chain the pipeline uses (bridge NI-walk, else forensic);
    'covenant_adjusted' only when at least one add-back is actually quantified (C5)."""
    ltm = None
    bridge = ov.get("economic_debt_bridge") or {}
    cv = bridge.get("ebitda")
    if cv and cv.get("value") is not None:
        ltm = float(cv["value"]) / 1e6
    else:
        for row in reversed(ov.get("forensic_table") or []):
            cv = row.get("ebitda")
            if cv and cv.get("value") is not None:
                ltm = float(cv["value"]) / 1e6
                break
    adj = None
    build = ov.get("ebitda_build") or {}
    addbacks = [a.get("amount") for a in build.get("addbacks") or []]
    quantified = [a["value"] for a in addbacks if a and a.get("value") is not None]
    if ltm is not None and quantified:
        adj = ltm + sum(quantified) / 1e6
    return {"ltm": ltm, "covenant_adjusted": adj}


def _face_accreted(ov: dict) -> dict[str, dict]:
    """{instrument[:80]: {face, accreted, oid}} in $mm. Accreted = carrying (GAAP
    effective-interest ≡ Moyer's original-issue-IRR accretion; ≤ 1 quarter stale);
    face = tagged DebtInstrumentFaceAmount, else carrying (no OID visible)."""
    out: dict[str, dict] = {}
    for i in ov.get("debt_schedule") or []:
        cv = i.get("outstanding") or i.get("principal")
        acc = (cv or {}).get("value")
        if not acc or acc <= 0:
            continue
        face = ((i.get("face_amount") or {}).get("value")) or acc
        out[(i.get("instrument") or "")[:80]] = {
            "face": face / 1e6, "accreted": acc / 1e6, "oid": face > acc * 1.005}
    return out


def build_creation_ladder(ov: dict, bonds: list[dict]) -> dict:
    """Ladder payload: classes in structural-priority order with cumulative face and
    market values, EBITDA variants, fulcrum class marker, and headline chips."""
    structure, _, _ = overview_to_structure(ov)
    quotes, notes = match_quotes(ov.get("debt_schedule") or [], bonds)
    quotes = {k[:80]: v for k, v in quotes.items()}   # adapter truncates tranche names to 80
    fa = _face_accreted(ov)   # dedup-suffixed (" *") tranche names miss -> face=carrying
    has_oid = any(v["oid"] for v in fa.values())
    ebitda = _ebitda_variants(ov)
    e = ebitda["ltm"]

    order = structure.priority_order()
    tmap = {t.name: t for t in structure.tranches}
    multi_entity = len({t.entity for t in structure.tranches}) > 1

    # fulcrum tranche at the reference multiple (first tranche short of full face)
    fulcrum_tranche = None
    if e is not None and e > 0 and order:
        wf = run_waterfall(structure, np.array([_FULCRUM_MULTIPLE * e]))
        for name in order:
            if float(wf[name][0]) < tmap[name].face - 1e-9:
                fulcrum_tranche = name
                break

    classes: list[dict] = []
    cum_face = cum_market = 0.0
    fulcrum_class_idx = None
    cum_accreted = 0.0
    for name in order:
        t = tmap[name]
        key = (t.entity, t.secured, t.preferred, t.lien_rank)
        quote = quotes.get(name)
        info = fa.get(name) or {"face": t.face, "accreted": t.face, "oid": False}
        # $mm market value: quoted price % of PRINCIPAL (face), never carrying — quotes
        # are % of face (Moyer ch. 5); accreted (= t.face) fallback when unquoted
        mkt = info["face"] * (quote["last_price"] / 100.0) if quote and quote.get("last_price") is not None else t.face
        if classes and classes[-1]["_key"] == key:
            c = classes[-1]
            c["members"].append(name)
            c["face"] += info["face"]
            c["accreted"] += t.face
            c["market"] += mkt
            c["quoted"] += 1 if quote else 0
        else:
            classes.append({
                "_key": key,
                "label": _class_label(t.secured, t.preferred, t.lien_rank, t.entity, multi_entity),
                "members": [name], "face": info["face"], "accreted": t.face, "market": mkt,
                "quoted": 1 if quote else 0,
            })
        if name == fulcrum_tranche:
            fulcrum_class_idx = len(classes) - 1

    creation_multiple_fulcrum = None
    for i, c in enumerate(classes):
        cum_face += c["face"]
        cum_accreted += c["accreted"]
        cum_market += c["market"]
        c["cum_face"] = round(cum_face, 1)
        c["cum_accreted"] = round(cum_accreted, 1)
        c["cum_market"] = round(cum_market, 1)
        c["face"] = round(c["face"], 1)
        c["accreted"] = round(c["accreted"], 1)
        c["market"] = round(c["market"], 1)
        c["unquoted"] = c["quoted"] < len(c["members"])
        c["is_fulcrum"] = i == fulcrum_class_idx
        if e is not None and e > 0:
            c["multiple_face"] = round(c["cum_face"] / e, 2)
            c["multiple_market"] = round(c["cum_market"] / e, 2)
            if c["is_fulcrum"]:
                creation_multiple_fulcrum = c["multiple_market"]
        else:
            c["multiple_face"] = c["multiple_market"] = None
        del c["_key"]

    # net-at-market leverage headline: (Σ market value of debt − cash) / EBITDA
    cash = None
    for row in reversed(ov.get("forensic_table") or []):
        cv = row.get("cash")
        if cv and cv.get("value") is not None:
            cash = float(cv["value"]) / 1e6
            break
    net_market_leverage = None
    if e is not None and e > 0 and classes:
        nml = (cum_market - (cash or 0.0)) / e
        net_market_leverage = derived_value(
            round(nml, 2),
            f"(Σ debt at market ${cum_market:,.0f}M − cash ${cash or 0:,.0f}M) ÷ EBITDA ${e:,.0f}M"
            + ("" if quotes else " — no quotes matched; debt at face"),
            fmt_ratio(round(nml, 2)),
            note="market values from TRACE drop-file where quoted, face otherwise",
        ).model_dump()

    # % of accreted value, never face, where OID exists (Moyer ch. 5) — a 65 quote on
    # 71.2 of accreted claim is 91.3, not distressed.
    def pct_of_accreted(n: str, q: dict) -> Optional[float]:
        p = q.get("last_price")
        if p is None:
            return None
        info = fa.get(n)
        return round(p * info["face"] / info["accreted"], 1) if info else p

    # min quoted price among unsecured instruments — one leg of the Moyer distress
    # fact pattern (stock < $1 AND unsecured debt > 40% discount); equity leg is client-side.
    # Re-based to % of accreted where OID (the raw quote overstates the discount).
    unsec_names = {(i.get("instrument") or "")[:80] for i in ov.get("debt_schedule") or []
                   if i.get("secured") is False}
    unsec_prices = [pct_of_accreted(n, q) if fa.get(n, {}).get("oid") else q["last_price"]
                    for n, q in quotes.items()
                    if n in unsec_names and q.get("last_price") is not None]

    return {
        "classes": classes,
        "min_unsecured_quote": min(unsec_prices) if unsec_prices else None,
        "quote_by_instrument": {n: {"last_price": q.get("last_price"),
                                    "last_yield": q.get("last_yield"),
                                    "as_of": q.get("as_of"),
                                    "pct_of_accreted": pct_of_accreted(n, q),
                                    "oid": bool(fa.get(n, {}).get("oid"))}
                                for n, q in quotes.items()},
        "has_oid": has_oid,
        "oid_note": ("quotes are % of principal; % of accreted value shown where OID — "
                     "unamortized discount is not a claim (Moyer ch. 5)") if has_oid else None,
        "ebitda_mm": ebitda,
        "fulcrum_class": classes[fulcrum_class_idx]["label"] if fulcrum_class_idx is not None else None,
        "fulcrum_note": f"fulcrum at {_FULCRUM_MULTIPLE:.1f}x EBITDA reference EV",
        "creation_multiple_fulcrum": creation_multiple_fulcrum,
        "net_market_leverage": net_market_leverage,
        "n_quoted": len(quotes),
        "n_instruments": len(order),
        "notes": notes,
        "derivation": "cumulative claims through each class ÷ LTM EBITDA; market = drop-file "
                      "quote % × face where matched (Moyer creation-value test)",
    }


# --------------------------------------------------------------------------- #
# F9: capacity-avoidance detector + mezzanine recast (Moyer ch. 6)
# --------------------------------------------------------------------------- #


def mezz_recast_row(ov: dict) -> Optional[dict]:
    """Synthetic debt-schedule row recasting temporary equity as a preferred claim.
    classify_seniority lands 'preferred' at rank 99/preferred=True — pays after debt,
    before common. None when no mezzanine was extracted."""
    cv = ov.get("mezzanine") or {}
    if not cv.get("value") or float(cv["value"]) <= 0:
        return None
    return {"instrument": "Mezzanine (recast as debt)", "outstanding": cv,
            "seniority": "preferred"}


def detect_capacity_avoidance(ov: dict, equity_price: Optional[float],
                              bonds: Optional[list[dict]] = None) -> dict:
    """Instruments engineered around leverage optics (Moyer ch. 6): busted converts
    (analyze purely as debt), PIK (cash coverage overstated), mezzanine preferred
    (recast as debt). Price-dependent — assembled on demand in the ladder endpoint;
    equity_price = Snapshot.last_price (set by a Default Risk run)."""
    from ..edgar.facts import fmt_money_millions

    quotes, _ = match_quotes(ov.get("debt_schedule") or [], bonds or [])
    items: list[dict] = []
    for inst in ov.get("debt_schedule") or []:
        name = inst.get("instrument") or ""
        q = quotes.get(name) or {}
        if inst.get("seniority") == "convertible" or q.get("convertible"):
            conv = (inst.get("conversion_price") or {}).get("value")
            if conv and equity_price is not None:
                ratio = round(float(equity_price) / float(conv), 2)
                busted = ratio < 0.5
                items.append({
                    "kind": "busted_convert", "instrument": name, "ratio": ratio,
                    "conversion_price": float(conv), "busted": busted,
                    "tone": "high" if busted else ("watch" if ratio < 1.0 else "neutral"),
                    "note": f"stock ${float(equity_price):,.2f} vs conversion "
                            f"${float(conv):,.2f} ({ratio:.2f}x)"
                            + (" — deeply busted; analyze purely as debt (Moyer ch. 6)"
                               if busted else
                               " — out of the money" if ratio < 1.0 else " — in the money"),
                })
            else:
                items.append({
                    "kind": "busted_convert", "instrument": name, "ratio": None,
                    "conversion_price": float(conv) if conv else None, "busted": None,
                    "tone": "neutral",
                    "note": ("conversion price not extracted — re-run the pipeline to extract"
                             if not conv else
                             "no equity price — run Default Risk once to set it"),
                })
        if inst.get("pik"):
            items.append({"kind": "pik", "instrument": name, "tone": "neutral",
                          "note": "PIK — cash interest deferred; cash coverage overstated"})
    mezz = (ov.get("mezzanine") or {}).get("value")
    if mezz and float(mezz) > 0:
        items.append({
            "kind": "mezzanine", "instrument": "Temporary equity (mezzanine)",
            "tone": "neutral", "amount_mm": round(float(mezz) / 1e6, 1),
            "note": f"temporary equity {fmt_money_millions(float(mezz))} — preferred with "
                    "debt-like redemption; recast as debt (carrying ≈ liquidation "
                    "preference + accrued dividends; may include redeemable NCI)"})
    return {
        "items": items, "equity_price": equity_price,
        "meta_note": ("capacity-avoidance instruments present — effective leverage is "
                      "already very high (Moyer ch. 6)") if items else None,
    }
