"""Company-options feasibility (Moyer ch. 11): with the clock running, what can the
company still do — buy in debt below par, exchange it, or sell assets? Deterministic
calculator over the cached overview + drop-file quotes; offer terms stay user input
(the SC TO-I / S-4 parser is Phase-6 backlog). The clock recomputes via
triage.events_from_ov (C4) — same numbers as the telegraph on the same page.
All $ figures $mm unless noted.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from ..edgar.facts import derived_value, fmt_money_millions
from .creation import _ebitda_variants, _face_accreted, build_creation_ladder
from .quotes import match_quotes
from .triage import events_from_ov

_EXCHANGE_DAYS = 60     # an exchange offer needs ~2 months to paper, launch and settle
_ASSET_SALE_DAYS = 180  # an asset sale needs ~6 months to market and close


def _latest(ov: dict, key: str) -> Optional[float]:
    for row in reversed(ov.get("forensic_table") or []):
        cv = row.get(key)
        if cv and cv.get("value") is not None:
            return float(cv["value"])
    return None


def _gate(days: Optional[int], need: int, what: str) -> dict:
    return {"days_to_next_event": days, "days_needed": need,
            "pass": None if days is None else bool(days >= need),
            "note": f"{what} needs ~{need} days to execute (Moyer ch. 11)"
                    + ("" if days is not None
                       else " — no dated events computable in the 24-month window")}


def build_options(ov: dict, bonds: list[dict]) -> dict:
    """The options-card payload: clock, buyback math, exchange gate, asset-sale
    explorer inputs, and the 4-axis feasibility chips."""
    sched = ov.get("debt_schedule") or []
    if not sched:
        return {"available": False,
                "note": "no debt schedule extracted — the options card needs per-issue "
                        "faces and quotes (the XBRL single-tranche seed carries neither)"}

    cal = events_from_ov(ov)          # C4: same event numbers as the telegraph
    cash = cal["cash"]                # raw $
    fcf = _latest(ov, "free_cash_flow")
    burn = max(-fcf, 0.0) if fcf is not None else 0.0
    quotes, quote_notes = match_quotes(sched, bonds)
    fa = _face_accreted(ov)           # $mm, name[:80]-keyed
    ladder = build_creation_ladder(ov, bonds)
    ebitda = _ebitda_variants(ov)["ltm"]
    try:
        asof = dt.date.fromisoformat(str(ov.get("debt_schedule_asof"))[:10])
    except (TypeError, ValueError):
        asof = dt.date.today()

    # ---- clock: who controls the timeline, and how long is it? -------------------
    days = None
    next_event = None
    if cal["events"]:
        e0 = cal["events"][0]
        days = (dt.date.fromisoformat(e0.date + "-01") - asof).days
        next_event = {"date": e0.date, "kind": e0.kind, "instrument": e0.instrument,
                      "amount": e0.amount.model_dump() if e0.amount else None}
    quoted_mv_mm = 0.0
    any_quote = False
    for name, q in quotes.items():
        info = fa.get(name[:80])
        if q.get("last_price") is None or info is None:
            continue
        any_quote = True
        quoted_mv_mm += info["face"] * q["last_price"] / 100.0
    fin_covs = any(pkg.get("financial_covenants") for pkg in ov.get("covenants") or [])
    if fin_covs:
        who, why = "creditors", ("financial covenants extracted — a test can hand "
                                 "control of the timeline to lenders")
    elif cash is not None and any_quote and cash / 1e6 > quoted_mv_mm:
        who, why = "company", ("no financial covenants extracted and cash exceeds the "
                               "market value of quoted debt — the company could buy in "
                               "its own paper")
    else:
        who, why = "unclear", ("no financial covenants extracted, but cash vs debt "
                               "market value is not decisive (or nothing is quoted)")
    runway_months = None
    if cal["liquidity_total"] and burn > 0:
        runway_months = round(cal["liquidity_total"] / (burn / 12.0), 1)
    clock = {
        "days_to_next_event": days, "next_event": next_event,
        "who_controls": who, "who_controls_note": why,
        "runway_months": runway_months,
        "healthsouth_flag": bool(runway_months is not None and runway_months < 1.0),
        "note": ("runway under a month — the option set collapses to filing or "
                 "forbearance almost overnight (the HealthSouth pattern, Moyer ch. 11)"
                 if runway_months is not None and runway_months < 1.0 else None),
    }

    # ---- buyback: what can open-market repurchases actually retire? --------------
    if cash is None:
        buyback = {"available": False,
                   "note": "no cash observation in the cached overview — re-run the "
                           "pipeline (Run live) to extract"}
    else:
        deployable = max(cash / 1e6 - burn / 1e6, 0.0)
        deployable_cv = derived_value(
            round(deployable, 1),
            f"max(cash {fmt_money_millions(cash)} − one year of burn "
            f"{fmt_money_millions(burn)}, 0) — required business investment proxied "
            "by one year of |FCF| burn, floored at 0",
            f"${deployable:,.0f}M").model_dump()
        rows: list[dict] = []
        for inst in sched:
            name = inst.get("instrument") or ""
            info = fa.get(name[:80])
            if info is None:
                continue
            price = (quotes.get(name) or {}).get("last_price")
            if price is not None and price > 0:
                market = round(info["face"] * price / 100.0, 1)
                retirable = min(info["face"], deployable * 100.0 / price)
                retirable_cv = derived_value(
                    round(retirable, 1),
                    f"min(face ${info['face']:,.0f}M, deployable ${deployable:,.0f}M "
                    f"÷ {price:g} per 100) — open-market repurchase at the drop-file "
                    "quote", f"${retirable:,.0f}M").model_dump()
                pct = (round(100.0 * retirable / info["face"], 1)
                       if info["face"] > 0 else None)
                feasible = bool(retirable > 0)
            else:
                market = retirable_cv = pct = feasible = None
            rows.append({"instrument": name[:80], "price": price,
                         "face_mm": round(info["face"], 1), "market_mm": market,
                         "retirable": retirable_cv, "retirable_pct": pct,
                         "feasible": feasible})
        rows.sort(key=lambda r: (r["price"] is None, r["price"] or 0.0))
        rp = ov.get("rp_basket") or {}
        if rp.get("covenant_status") == "none":
            rp_note = ("no RP covenant extracted — repurchases contractually "
                       "unrestricted (Moyer ch. 9)")
        elif rp.get("capacity") is not None:
            rp_note = (f"RP basket capacity {(rp['capacity'] or {}).get('display')} — "
                       "a below-par repurchase may need basket room (see Covenant "
                       "dollars)")
        else:
            rp_note = ("RP basket not in this cached overview — re-run the pipeline "
                       "to extract")
        buyback = {"available": True, "deployable": deployable_cv, "rows": rows,
                   "rp_note": rp_note,
                   "derivation": "deployable = max(cash − max(−FCF, 0), 0); retirable "
                                 "face = min(face, deployable ÷ price per 100), "
                                 "cheapest quote first (Moyer ch. 11)"}

    # ---- exchange gate: is an exchange offer even worth papering? ----------------
    min_unsec = ladder.get("min_unsecured_quote")
    capture = round(100.0 - min_unsec, 1) if min_unsec is not None else None
    n_unsec = sum(1 for c in ladder.get("classes") or []
                  if c["label"].startswith("Unsecured"))
    lh = ov.get("liens_headroom") or {}
    arch = lh.get("archetype")
    if not lh or not lh.get("available"):
        claim_status = "not computed — see the Covenant dollars card"
    elif arch == "unbounded":
        claim_status = ("secured exchange available — no lien covenant governs the "
                        "notes (unbounded priming, Moyer ch. 9)")
    elif arch == "computed":
        sp = lh.get("suggested_priming") or {}
        claim_status = (f"computed lien headroom ${sp.get('value'):,.0f}M — a secured "
                        "exchange can improve claim status inside it"
                        if sp.get("value") else
                        "computed lien headroom — a secured exchange can improve "
                        "claim status inside it")
    elif arch == "stated_capacity":
        claim_status = ("stated lien capacity (utilization unknown) — a secured "
                        "exchange may fit inside it")
    else:
        claim_status = ("lien covenant present but capacity unquantified — "
                        "claim-status uplift uncertain")
    gate60 = _gate(days, _EXCHANGE_DAYS, "an exchange offer")
    reasons: list[str] = []
    if gate60["pass"] is False:
        reasons.append(f"next liquidity event in {days} days — inside the "
                       f"~{_EXCHANGE_DAYS}-day exchange window")
    if capture is None:
        reasons.append("no unsecured quote matched — discount capture unknown")
    elif capture <= 2.0:
        reasons.append("unsecured quotes near par — nothing to capture")
    else:
        reasons.append(f"{capture:g} points of discount capturable on the cheapest "
                       "unsecured issue")
    verdict = ("no_window" if gate60["pass"] is False else
               "viable" if capture is not None and capture > 2.0 else
               "nothing_to_capture" if capture is not None else "unknown")
    if n_unsec == 0:
        holdout_note = ("no unsecured classes — an exchange would target secured "
                        "paper (claim status can only be preserved, not improved)")
    elif n_unsec == 1:
        holdout_note = ("single unsecured class — holdouts are pivotal; exit consents "
                        "and a minimum-tender condition carry the offer (Moyer ch. 11)")
    else:
        holdout_note = (f"{n_unsec} unsecured classes — holdout risk diffuses across "
                        "classes but inter-class priority fights emerge")
    exchange_gate = {
        "gate_60d": gate60,
        "discount_capture_per_100": capture,
        "min_unsecured_quote": min_unsec,
        "n_unsecured_classes": n_unsec,
        "holdout_note": holdout_note,
        "claim_status": claim_status,
        "verdict": verdict, "reasons": reasons,
    }

    # ---- asset-sale explorer inputs (arithmetic is client-side, F7 precedent) ----
    classes = ladder.get("classes") or []
    all_prices = [q["last_price"] for q in quotes.values()
                  if q.get("last_price") is not None]
    asset_sale = {
        "ebitda_mm": ebitda,
        "total_face_mm": classes[-1]["cum_face"] if classes else None,
        "total_market_mm": classes[-1]["cum_market"] if classes else None,
        "quote_min": min(all_prices) if all_prices else None,
        "gate_6mo": _gate(days, _ASSET_SALE_DAYS, "an asset sale"),
        "note": ("n.m. — non-positive EBITDA; value asset sales against the "
                 "liquidation panel instead"
                 if ebitda is None or ebitda <= 0 else None),
        "derivation": "client-side explorer: pro-forma leverage = (face − retired) ÷ "
                      "(EBITDA − sold); implied stub price = market-implied multiple "
                      "(Σ market ÷ EBITDA) × remaining EBITDA ÷ stub face "
                      "(Moyer ch. 11)",
    }

    # ---- the 4 feasibility axes (computed chips only — C3 cut) -------------------
    def axis(key: str, label: str, tone: str, detail: str) -> dict:
        return {"key": key, "label": label, "tone": tone, "detail": detail}

    axes = [axis(
        "time_to_event",
        f"clock: {days}d to next event" if days is not None
        else "clock: no dated events",
        "neutral" if days is None else
        "high" if days < _EXCHANGE_DAYS else
        "watch" if days < _ASSET_SALE_DAYS else "ok",
        "time to the next coupon/maturity bounds which options are executable")]
    if cash is None:
        axes.append(axis("deployable_liquidity", "deployable: unknown", "neutral",
                         "no cash observation — re-run the pipeline to extract"))
    else:
        dep = max(cash / 1e6 - burn / 1e6, 0.0)
        axes.append(axis("deployable_liquidity", f"deployable ${dep:,.0f}M",
                         "ok" if dep > 0 else "high",
                         "cash less one year of burn — what a buyback can spend"))
    rp = ov.get("rp_basket") or {}
    cap_v = (rp.get("capacity") or {}).get("value")
    if not rp:
        axes.append(axis("covenant_room", "covenant room: not extracted", "neutral",
                         "re-run the pipeline to build the RP basket"))
    elif rp.get("covenant_status") == "none":
        axes.append(axis("covenant_room", "covenant room: unrestricted", "ok",
                         "no RP covenant extracted — repurchases/distributions "
                         "contractually unrestricted"))
    elif cap_v is None:
        axes.append(axis("covenant_room", "covenant room: not computable", "neutral",
                         "no quarterly flow facts in the window — see Covenant dollars"))
    elif cap_v > 0:
        axes.append(axis("covenant_room", f"covenant room: ${cap_v:,.0f}M RP", "ok",
                         "cumulative RP-basket capacity (see Covenant dollars)"))
    else:
        axes.append(axis("covenant_room", "covenant room: $0 RP basket", "high",
                         "builder negative or zero — basket-gated actions blocked"))
    if not lh or not lh.get("available"):
        axes.append(axis("secured_headroom", "secured headroom: not computed",
                         "neutral", "see the Covenant dollars card"))
    elif arch == "unbounded":
        axes.append(axis("secured_headroom", "secured headroom: unbounded", "ok",
                         "no lien covenant governs the notes — secured new money / "
                         "exchange available (the company's option, the creditors' "
                         "risk)"))
    elif arch == "computed":
        sp = lh.get("suggested_priming") or {}
        axes.append(axis("secured_headroom",
                         f"secured headroom: ${sp.get('value') or 0:,.0f}M", "ok",
                         sp.get("basis") or "computed NTA headroom"))
    elif arch == "stated_capacity":
        axes.append(axis("secured_headroom", "secured headroom: stated capacity",
                         "watch", "stated $ capacity — utilization unknown"))
    else:
        axes.append(axis("secured_headroom", "secured headroom: unquantified",
                         "watch", "ratio-gated or unquantified lien covenant"))

    return {
        "available": True,
        "clock": clock,
        "buyback": buyback,
        "exchange_gate": exchange_gate,
        "asset_sale_inputs": asset_sale,
        "axes": axes,
        "quote_notes": quote_notes,
        "derivation": "deterministic feasibility read over the cached overview + "
                      "drop-file quotes; offer terms are user input — see the "
                      "Exchange analyzer on the Recovery page (Moyer ch. 11)",
    }
