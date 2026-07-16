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


def build_creation_ladder(ov: dict, bonds: list[dict]) -> dict:
    """Ladder payload: classes in structural-priority order with cumulative face and
    market values, EBITDA variants, fulcrum class marker, and headline chips."""
    structure, _, _ = overview_to_structure(ov)
    quotes, notes = match_quotes(ov.get("debt_schedule") or [], bonds)
    quotes = {k[:80]: v for k, v in quotes.items()}   # adapter truncates tranche names to 80
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
    for name in order:
        t = tmap[name]
        key = (t.entity, t.secured, t.preferred, t.lien_rank)
        quote = quotes.get(name)
        # $mm market value: quoted price % of face, else face (flagged)
        mkt = t.face * (quote["last_price"] / 100.0) if quote and quote.get("last_price") is not None else t.face
        if classes and classes[-1]["_key"] == key:
            c = classes[-1]
            c["members"].append(name)
            c["face"] += t.face
            c["market"] += mkt
            c["quoted"] += 1 if quote else 0
        else:
            classes.append({
                "_key": key,
                "label": _class_label(t.secured, t.preferred, t.lien_rank, t.entity, multi_entity),
                "members": [name], "face": t.face, "market": mkt,
                "quoted": 1 if quote else 0,
            })
        if name == fulcrum_tranche:
            fulcrum_class_idx = len(classes) - 1

    creation_multiple_fulcrum = None
    for i, c in enumerate(classes):
        cum_face += c["face"]
        cum_market += c["market"]
        c["cum_face"] = round(cum_face, 1)
        c["cum_market"] = round(cum_market, 1)
        c["face"] = round(c["face"], 1)
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

    # min quoted price among unsecured instruments — one leg of the Moyer distress
    # fact pattern (stock < $1 AND unsecured debt > 40% discount); equity leg is client-side.
    unsec_names = {(i.get("instrument") or "")[:80] for i in ov.get("debt_schedule") or []
                   if i.get("secured") is False}
    unsec_prices = [q["last_price"] for n, q in quotes.items()
                    if n in unsec_names and q.get("last_price") is not None]

    return {
        "classes": classes,
        "min_unsecured_quote": min(unsec_prices) if unsec_prices else None,
        "quote_by_instrument": {n: {"last_price": q.get("last_price"),
                                    "last_yield": q.get("last_yield"),
                                    "as_of": q.get("as_of")} for n, q in quotes.items()},
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
