"""Bank-debt triage + filing telegraph (Moyer ch. 8): where the bank sits when trouble
starts (secured? covered at trough?) and the five disclosure/behavior tells that a
filing is being telegraphed. Deterministic — the FTS scan quotes stored filings, the
coverage read runs the same waterfall the Recovery page uses; nothing predicts.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Optional

import numpy as np

from ..edgar.facts import derived_value, fmt_money_millions
from ..fulcrum.adapter import classify_seniority, overview_to_structure
from ..fulcrum.waterfall import run_waterfall
from ..schemas import DebtInstrument
from .liquidity import build_event_calendar

# facility_type values debt_xbrl.facility_type_of can emit that mean "bank paper"
BANK_TYPES = {"revolver", "term loan", "delayed-draw term loan", "credit facility",
              "bridge loan"}
# name fallback — load-bearing: AAL tags facility_type on none of its 14 rows.
# "Special Facility Revenue Bonds" and commercial paper correctly miss.
_BANK_NAME_RE = re.compile(r"revolv|term loan|credit facilit|\babl\b|\bddtl\b|bridge loan",
                           re.IGNORECASE)

_STATE_NOTES = {
    "no_bank_debt": "no bank facilities identified — bond-only structure; the telegraph "
                    "signals carry the read",
    "security_grab": "unsecured bank exposure — an unsecured bank lender's first move in "
                     "a workout is to demand security (Moyer ch. 8)",
    "undersecured_watch": "bank short at trough EV (4.0× EBITDA) — expect collateral "
                          "tightening and amendment fees before any waiver",
    "filing_pretext": "bank undersecured on a liquidation basis at non-positive EBITDA — "
                      "a covenant breach becomes the filing pretext, not a waiver "
                      "negotiation (Moyer ch. 8)",
    "waiver_path": "bank fully covered at the test point (trough EV / orderly "
                   "liquidation) — breaches route to waivers and amendments, not "
                   "acceleration (Moyer ch. 8)",
    "coverage_unknown": "coverage not computable — no positive EBITDA and no asset "
                        "snapshot extracted",
}

_NOTES = [
    "coverage assumes an all-asset pledge: the bank shares pari passu with same-lien "
    "non-bank secured paper (no per-facility collateral pools extracted)",
    "bank lenders' economics differ from bondholders' (relationship, fees, par holders) — "
    "the state machine reads structure, not holder behavior",
]


def _is_bank(inst: dict) -> bool:
    """facility_type when tagged, name regex otherwise."""
    ft = inst.get("facility_type")
    if ft is not None:
        return ft in BANK_TYPES
    return bool(_BANK_NAME_RE.search(inst.get("instrument") or ""))


def events_from_ov(ov: dict) -> dict:
    """Recompute the 24-month event calendar + liquidity from a raw overview dict —
    stale-cache-proof (pre-Phase-1 snapshots cache liquidity_events as [] and carry no
    liquidity block). Shared by the telegraph and the options card (C4): same numbers
    on the same page."""
    sched = [DebtInstrument.model_validate(i) for i in ov.get("debt_schedule") or []]
    cash = None
    for row in reversed(ov.get("forensic_table") or []):
        cv = row.get("cash")
        if cv and cv.get("value") is not None:
            cash = float(cv["value"])
            break
    undrawn = sum(float(i.undrawn.value) for i in sched
                  if i.undrawn and i.undrawn.value and i.undrawn.value > 0)
    liquidity_total = ((cash or 0.0) + undrawn
                       if (cash is not None or undrawn > 0) else None)
    ebitda = ((ov.get("economic_debt_bridge") or {}).get("ebitda") or {}).get("value")
    if ebitda is None:
        for row in reversed(ov.get("forensic_table") or []):
            cv = row.get("ebitda")
            if cv and cv.get("value") is not None:
                ebitda = float(cv["value"])
                break
    events, note = build_event_calendar(sched, liquidity_total, ebitda,
                                        ov.get("debt_schedule_asof"))
    return {"events": events, "liquidity_total": liquidity_total, "cash": cash,
            "note": note}


# --------------------------------------------------------------------------- #
# F5: bank triage
# --------------------------------------------------------------------------- #


def bank_triage(ov: dict) -> dict:
    """Where the bank sits (Moyer ch. 8): drawn/undrawn, security, coverage at trough
    (4.0×) and base (6.0×) EBITDA — orderly liquidation basis when EBITDA ≤ 0."""
    sched = ov.get("debt_schedule") or []
    if not sched:
        return {"available": False,
                "note": "no debt schedule extracted — bank triage unavailable (the XBRL "
                        "single-tranche seed carries no facility detail)"}

    rows: list[dict] = []
    drawn_parts: list[str] = []
    undrawn_parts: list[str] = []
    drawn_sum = undrawn_sum = 0.0
    bank_names: set[str] = set()
    any_unsecured = False
    for inst in sched:
        if not _is_bank(inst):
            continue
        name = inst.get("instrument") or ""
        secured, lien, _ = classify_seniority(inst.get("seniority"),
                                              inst.get("secured"), name)
        cv = inst.get("outstanding") or inst.get("principal")
        amt = (cv or {}).get("value")
        if amt and float(amt) > 0:
            drawn_sum += float(amt)
            drawn_parts.append(f"{name} {fmt_money_millions(float(amt))}")
            bank_names.add(name[:80])
            if not secured:   # only drawn exposure counts — agreement-shell rows
                any_unsecured = True   # (outstanding None) carry no security to grab
        u = (inst.get("undrawn") or {}).get("value")
        if u and float(u) > 0:
            undrawn_sum += float(u)
            undrawn_parts.append(f"{name} {fmt_money_millions(float(u))}")
        rows.append({
            "instrument": name, "facility_type": inst.get("facility_type"),
            "drawn": cv, "undrawn": inst.get("undrawn"),
            "secured": secured, "lien_rank": lien,
            "secured_source": "tagged" if inst.get("secured") is not None
                              else "name-heuristic",
        })

    drawn_total = (derived_value(drawn_sum, " + ".join(drawn_parts),
                                 fmt_money_millions(drawn_sum)).model_dump()
                   if drawn_parts else None)
    undrawn_total = (derived_value(undrawn_sum, " + ".join(undrawn_parts),
                                   fmt_money_millions(undrawn_sum)).model_dump()
                     if undrawn_parts else None)

    if not rows:
        return {"available": True, "state": "no_bank_debt",
                "state_note": _STATE_NOTES["no_bank_debt"], "rows": [],
                "drawn_total": None, "undrawn_total": None, "coverage": None,
                "notes": _NOTES}

    # coverage: same waterfall as the Recovery page (going concern), orderly
    # liquidation when positive EBITDA is unattainable (Moyer ch. 5)
    structure, ebitda, _ = overview_to_structure(ov)
    bank_tranches = [t for t in structure.tranches
                     if t.name in bank_names or t.name.rstrip(" *") in bank_names]
    coverage = None
    covered_at_trough = None
    if bank_tranches:
        bank_claim = sum(t.face for t in bank_tranches)
        if ebitda is not None and ebitda > 0:
            evs = np.array([4.0 * ebitda, 6.0 * ebitda])   # trough / base, one run
            wf = run_waterfall(structure, evs)
            rec = sum(wf[t.name] for t in bank_tranches)
            coverage = {"basis": "going_concern", "bank_claim_mm": round(bank_claim, 1),
                        "points": [{"multiple": m, "ev_mm": round(float(e), 1),
                                    "coverage_pct": round(100 * float(rec[k]) / bank_claim, 1)}
                                   for k, (m, e) in enumerate(zip((4.0, 6.0), evs))]}
            covered_at_trough = coverage["points"][0]["coverage_pct"] >= 99.9
        else:
            from ..fulcrum.liquidation import assets_from_snapshot, liquidate
            assets = assets_from_snapshot(ov.get("asset_snapshot"))
            if assets is not None:
                out = liquidate(assets, structure)          # orderly preset
                recs = {r["tranche"]: r["recovery"] for r in out["scenario"]["tranches"]}
                rec = sum(recs.get(t.name, 0.0) for t in bank_tranches)
                coverage = {"basis": "liquidation", "bank_claim_mm": round(bank_claim, 1),
                            "net_proceeds_mm": out["scenario"]["net_proceeds"],
                            "coverage_pct": round(100 * rec / bank_claim, 1)}
                covered_at_trough = coverage["coverage_pct"] >= 99.9

    if any_unsecured:
        state = "security_grab"
    elif coverage is None:
        state = "coverage_unknown"
    elif covered_at_trough:
        state = "waiver_path"
    elif ebitda is not None and ebitda <= 0:
        state = "filing_pretext"
    else:
        state = "undersecured_watch"

    return {"available": True, "state": state, "state_note": _STATE_NOTES[state],
            "rows": rows, "drawn_total": drawn_total, "undrawn_total": undrawn_total,
            "coverage": coverage, "notes": _NOTES}


# --------------------------------------------------------------------------- #
# F6: filing telegraph — five signals, unknowns out of the score denominator
# --------------------------------------------------------------------------- #

_DEFAULT_PHRASES = ("event of default", "notice of default", "forbearance",
                    "covenant waiver")
_ADVISOR_PHRASES = ("going concern", "substantial doubt", "restructuring advisor",
                    "financial advisor")


def _source_date(session, kind: str, ref_id) -> Optional[dt.date]:
    from .. import models
    try:
        rid = int(ref_id)
    except (TypeError, ValueError):
        return None
    if kind == "mdna":
        row = session.get(models.MdnaSection, rid)
        return row.period_end if row else None
    row = session.get(models.FilingNotes, rid)
    if row is None or not row.filing_date:
        return None
    try:
        return dt.date.fromisoformat(str(row.filing_date)[:10])
    except ValueError:
        return None


def _fts_scan(session, ticker: str, phrases: tuple[str, ...],
              asof: dt.date) -> Optional[list[dict]]:
    """Phrase hits in MD&A/statement notes filed within 12 months of asof. Returns None
    when FTS5 is unavailable (the signal reads unknown). Phrases are server-owned and
    quoted, so FTS query syntax can't be hit."""
    from ..core import db as core_db
    if session is None or not core_db.FTS_AVAILABLE:
        return None
    from sqlalchemy import text as sql
    cutoff = asof - dt.timedelta(days=365)
    hits: list[dict] = []
    for ph in phrases:
        match = '"' + ph.replace('"', '""') + '"'
        try:
            found = session.execute(sql(
                "SELECT source_kind, ref_id, "
                "snippet(search, 0, '<mark>', '</mark>', ' ... ', 24) "
                "FROM search WHERE search MATCH :q AND ticker = :t "
                "AND source_kind IN ('mdna', 'notes') ORDER BY bm25(search) LIMIT 4"),
                {"q": match, "t": ticker}).all()
        except Exception:
            return None
        for kind, ref_id, snip in found:
            d = _source_date(session, kind, ref_id)
            if d is None or d < cutoff:   # recency-joined to period_end / filing_date
                continue
            hits.append({"phrase": ph, "source_kind": kind, "ref_id": ref_id,
                         "date": d.isoformat(), "snippet": snip})
    return hits


def filing_telegraph(ov: dict, session) -> dict:
    """The five ch. 8 telegraph signals, each on|off|unknown with evidence; score keeps
    unknowns out of the denominator. Context (unscored): distress read + covenant list
    (test dates aren't extracted — no breach prediction)."""
    ticker = ((ov.get("header") or {}).get("ticker") or "").strip().upper()
    try:
        asof = dt.date.fromisoformat(str(ov.get("debt_schedule_asof"))[:10])
    except (TypeError, ValueError):
        asof = dt.date.today()

    signals: list[dict] = []

    # (1) coupon dates become focal — recomputed, never read from the cached calendar
    cal = events_from_ov(ov)
    cash = cal["cash"]
    coupons = [e for e in cal["events"] if e.kind == "coupon"]
    amount = None
    if not coupons:
        state = "unknown"
        detail = "no coupon events computable (no tagged rates or maturities)"
    else:
        nxt = coupons[0]
        amount = nxt.amount.model_dump()
        at_risk = "coupon_at_risk" in nxt.flags
        over_cash = bool(cash is not None and nxt.amount.value
                         and nxt.amount.value > cash)
        state = "on" if (at_risk or over_cash) else "off"
        detail = (f"next coupon {nxt.date} — {nxt.instrument}: {nxt.amount.display} vs "
                  f"cash {fmt_money_millions(cash) or 'n/a'}; unpaid, default "
                  f"crystallizes ~30 days after {nxt.date}")
    signals.append({"key": "coupon_focal", "label": "Coupon dates become focal",
                    "state": state, "detail": detail, "amount": amount, "evidence": [],
                    "assumption": "30-day grace period assumed before a missed coupon "
                                  "matures into an event of default (fixed assumption — "
                                  "indenture grace periods not extracted)"})

    # (2) + (4) — ONE scan function, two phrase lists (mdna/notes only, 12-mo recency)
    for key, label, phrases in (
            ("default_disclosure", "Default/forbearance language in filings",
             _DEFAULT_PHRASES),
            ("advisor_going_concern", "Going-concern / advisor language",
             _ADVISOR_PHRASES)):
        hits = _fts_scan(session, ticker, phrases, asof)
        if hits is None:
            state, detail = "unknown", "full-text index unavailable in this build"
        elif hits:
            state = "on"
            detail = f"{len(hits)} phrase hit(s) in MD&A/notes filed within 12 months"
        else:
            state, detail = "off", "no phrase hits in MD&A/notes within 12 months"
        signals.append({"key": key, "label": label, "state": state, "detail": detail,
                        "evidence": (hits or [])[:6],
                        "assumption": "FTS phrase scan over stored MD&A + statement "
                                      "notes; no 8-K corpus until Phase 6 (Item 2.04 "
                                      "not scanned)"})

    # (3) revolver drawn into a war chest — point-in-time balance-sheet read
    sched = ov.get("debt_schedule") or []
    drawn = sum(float(((i.get("outstanding") or i.get("principal")) or {}).get("value") or 0)
                for i in sched if _is_bank(i))
    undrawn = sum(float((i.get("undrawn") or {}).get("value") or 0)
                  for i in sched if _is_bank(i))
    cash_rows = [float((r.get("cash") or {}).get("value"))
                 for r in ov.get("forensic_table") or []
                 if (r.get("cash") or {}).get("value") is not None]
    if drawn <= 0:
        state, detail = "off", "no drawn bank credit — no war chest being built"
    elif undrawn <= 0:
        state = "on"
        detail = (f"bank credit fully drawn ({fmt_money_millions(drawn)}) with no tagged "
                  "headroom left — cash hoarded ahead of trouble (Moyer ch. 8)")
    elif len(cash_rows) < 2:
        state = "unknown"
        detail = "fewer than two cash observations — the cash-spike leg is not evaluable"
    elif cash_rows[-1] > 1.25 * cash_rows[-2]:
        state = "on"
        detail = (f"cash {fmt_money_millions(cash_rows[-1])} up >25% vs prior period "
                  f"({fmt_money_millions(cash_rows[-2])}) with "
                  f"{fmt_money_millions(drawn)} of bank credit drawn — a war chest; a "
                  "cash spike can also be an equity raise or asset sale (point-in-time "
                  "read, no drawdown time series)")
    else:
        state, detail = "off", "bank credit drawn, but no cash spike and headroom remains"
    signals.append({"key": "war_chest", "label": "Revolver drawn into a war chest",
                    "state": state, "detail": detail, "evidence": [],
                    "assumption": "point-in-time balance-sheet read — revolver drawdown "
                                  "time series not available"})

    # (5) payables stretched — verbatim reuse of the forensic flag (zero new detection)
    if len(ov.get("forensic_table") or []) < 2:
        state = "unknown"
        detail = "fewer than two fiscal periods — payables trend not evaluable"
    else:
        flag = next((f for f in ov.get("forensic_flags") or []
                     if f.get("flag_type") == "ap_outrunning_revenue"), None)
        state = "on" if flag else "off"
        detail = (flag.get("narrative") if flag
                  else "payables tracking the business — no stretch flag")
    signals.append({"key": "payables_stretch", "label": "Payables being stretched",
                    "state": state, "detail": detail, "evidence": [],
                    "assumption": "verbatim reuse of the forensic ap_outrunning_revenue "
                                  "flag (DPO climb / level tests)"})

    covenant_context = []
    for pkg in ov.get("covenants") or []:
        for c in pkg.get("financial_covenants") or []:
            covenant_context.append({
                "family": pkg.get("family_label") or pkg.get("agreement_type"),
                "kind": c.get("kind"), "threshold": c.get("threshold"),
                "test_frequency": c.get("test_frequency")})

    return {
        "signals": signals,
        "score": {"on": sum(1 for s in signals if s["state"] == "on"),
                  "evaluable": sum(1 for s in signals if s["state"] != "unknown")},
        "as_of": asof.isoformat(),
        "context": {
            "is_distressed": bool((ov.get("liquidity") or {}).get("is_distressed")),
            "covenants": covenant_context,
            "note": "covenant test dates are not extracted — no breach prediction; the "
                    "covenant list is context for the disclosure scan (Moyer ch. 8)",
        },
    }
