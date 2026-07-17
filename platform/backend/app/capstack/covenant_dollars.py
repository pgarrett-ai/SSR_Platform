"""Covenant dollars (Moyer ch. 7/9): the RP-basket builder — how much value can leak
to shareholders — and permitted-liens headroom — how much new secured debt can prime
you. Deterministic regex over already-cached covenant extractions plus XBRL quarterly
flows; no re-extraction (both heroes' operative RP/lien sections sit outside the
extraction window — prompt-v3 + window targeting is Phase-3+ backlog).
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Optional

from ..edgar.facts import derived_value
from ..schemas import RpBasket, RpBasketPoint

FLOW_KEYS = ("net_income", "equity_issuance_proceeds", "dividends_paid",
             "stock_repurchases")

_RP_NAME_RE = re.compile(r"restricted payment|\brp\b", re.IGNORECASE)
_LIEN_RE = re.compile(r"lien|negative pledge|incremental|priming|secured", re.IGNORECASE)
_RATIO_COV_RE = re.compile(r"loan[- ]to[- ]value|\bltv\b|collateral coverage|\bccr\b",
                           re.IGNORECASE)
_NTA_RE = re.compile(r"net tangible assets", re.IGNORECASE)

_DOLLAR_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")
_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_RATIO_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:to|:)\s*1(?:\.0+)?\b")

_OMISSIONS_NOTE = ("omits junior-debt-issuance proceeds and unrestricted-subsidiary "
                   "investment returns (not deterministically extractable); cumulation "
                   "anchors at the lookback-window start — a proxy for the "
                   "issuance-date build convention")
_UNBOUNDED_LEAKAGE = ("no RP covenant extracted — distributions contractually "
                      "unrestricted (unbounded leakage, Moyer ch. 9)")


def parse_capacity_tokens(text: Optional[str]) -> dict:
    """$ / % / 'N to 1.0' ratio tokens from an extracted covenant string.
    Dollars in $mm (max token wins), pct as a decimal fraction, ratio as a float."""
    t = text or ""
    dollars = [float(m.replace(",", "")) for m in _DOLLAR_RE.findall(t)]
    pcts = _PCT_RE.findall(t)
    ratios = _RATIO_RE.findall(t)
    return {
        "dollars": round(max(dollars) / 1e6, 1) if dollars else None,
        "pct": float(pcts[0]) / 100.0 if pcts else None,
        "ratio": float(ratios[0]) if ratios else None,
    }


# --------------------------------------------------------------------------- #
# F1: RP-basket builder
# --------------------------------------------------------------------------- #


def rp_basket_from_flows(flows: dict, covenants: list[dict], years: int) -> RpBasket:
    """Assemble the basket from per-quarter flow series (raw $, quarterly_flows shape)
    plus the cached covenant layer. Pure — unit-tested on hand-built series.

    Per quarter (Moyer ch. 7): contribution = (0.5×NI if NI>0 else 1.0×NI)
    + equity proceeds − dividends − buybacks; capacity = starter + Σ contributions."""
    today = dt.date.today()
    start = today.replace(year=today.year - years, day=min(today.day, 28))
    series = {k: {pe: v for pe, v in flows.get(k) or [] if pe >= start}
              for k in FLOW_KEYS}
    quarters = sorted(set().union(*(set(s) for s in series.values())))

    # covenant layer: RP-named basket facts; a $-token fills the starter
    rp_facts = []
    for pkg in covenants or []:
        fam = pkg.get("family_label") or pkg.get("agreement_type")
        for b in pkg.get("baskets") or []:
            if _RP_NAME_RE.search(b.get("name") or ""):
                rp_facts.append((fam, b))
    starter_mm, status = 0.0, "none"
    fam = value = quote = None
    for f, b in rp_facts:
        tok = parse_capacity_tokens(b.get("value"))
        if tok["dollars"] is not None:
            starter_mm, status = tok["dollars"], "extracted"
            fam, value, quote = f, b.get("value"), b.get("quote")
            break
    else:
        if rp_facts:
            status = "stub"
            fam, value = rp_facts[0][0], rp_facts[0][1].get("value")
            quote = rp_facts[0][1].get("quote")

    notes: list[str] = []
    if status == "none":
        notes.append(_UNBOUNDED_LEAKAGE)
    elif status == "stub":
        notes.append(f"issuer RP basket extracted as a stub ('{(value or '')[:80]}') — "
                     "builder-only starter $0")

    missing = [k for k in FLOW_KEYS if not series[k]]
    formula_note = (", ".join(missing) + " not tagged in XBRL — treated as $0"
                    if missing else None)

    if not quarters:
        return RpBasket(available=False, covenant_status=status,
                        covenant_family=fam, covenant_value=value, covenant_quote=quote,
                        formula_note=formula_note,
                        notes=notes + ["no quarterly flow facts in the window — "
                                       "basket build unavailable"])

    points: list[RpBasketPoint] = []
    cum = 0.0
    for pe in quarters:
        ni = series["net_income"].get(pe)
        ni_credit = (0.5 * ni if ni > 0 else ni) if ni is not None else 0.0
        eq = series["equity_issuance_proceeds"].get(pe) or 0.0
        div = series["dividends_paid"].get(pe) or 0.0
        buy = series["stock_repurchases"].get(pe) or 0.0
        contrib = (ni_credit + eq - div - buy) / 1e6
        cum += contrib
        points.append(RpBasketPoint(
            label=f"Q{(pe.month - 1) // 3 + 1} {pe.year}", period_end=pe.isoformat(),
            net_income=round(ni / 1e6, 1) if ni is not None else None,
            ni_credit=round(ni_credit / 1e6, 1), equity_proceeds=round(eq / 1e6, 1),
            dividends=round(div / 1e6, 1), buybacks=round(buy / 1e6, 1),
            contribution=round(contrib, 1), cumulative=round(cum, 1)))

    starter = derived_value(
        starter_mm,
        (f"$-token from RP basket fact: {(value or '')[:80]}" if status == "extracted"
         else "builder-only (starter basket not extracted) — $0 assumed"),
        f"${starter_mm:,.0f}M", note=(quote or "")[:200] or None)

    raw = starter_mm + cum
    cap_v = max(round(raw, 1), 0.0)
    cap_note = _OMISSIONS_NOTE
    if raw < 0:
        cap_note += "; builder negative — cumulative deductions exceed credits, $0 capacity"
    capacity = derived_value(
        cap_v,
        f"starter ${starter_mm:,.0f}M + Σ quarterly [0.5×NI if NI>0 else 1.0×NI "
        f"+ equity issuance − dividends − buybacks] over "
        f"{points[0].label}→{points[-1].label} (${cum:,.0f}M), floored at 0",
        f"${cap_v:,.0f}M", note=cap_note)

    return RpBasket(available=True, covenant_status=status, starter=starter,
                    points=points, capacity=capacity, formula_note=formula_note,
                    covenant_family=fam, covenant_value=value, covenant_quote=quote,
                    notes=notes)


def build_rp_basket(entity_facts, covenants: list[dict], years: int) -> RpBasket:
    """Pipeline entry: pull the four quarterly flow series from XBRL, then assemble."""
    from ..edgar.facts import _SPEC_BY_KEY, quarterly_flows
    flows = {k: quarterly_flows(entity_facts, _SPEC_BY_KEY[k]) for k in FLOW_KEYS}
    return rp_basket_from_flows(flows, covenants, years)


# --------------------------------------------------------------------------- #
# F2: permitted-liens headroom
# --------------------------------------------------------------------------- #


def _lien_facts(pkg: dict) -> list[dict]:
    """Lien-relevant facts in one covenant package: LTV/CCR financial covenants
    (matched on kind + threshold — FinancialCovenant has no name field) plus
    lien-named baskets."""
    facts = []
    for c in pkg.get("financial_covenants") or []:
        if _RATIO_COV_RE.search(f"{c.get('kind') or ''} {c.get('threshold') or ''}"):
            facts.append({"name": c.get("kind"), "value": c.get("threshold"),
                          "quote": c.get("quote"), "_ratio_cov": True})
    for b in pkg.get("baskets") or []:
        if _LIEN_RE.search(b.get("name") or ""):
            facts.append({"name": b.get("name"), "value": b.get("value"),
                          "quote": b.get("quote"), "_ratio_cov": False})
    return facts


def _nta_mm(ov: dict) -> tuple[Optional[float], Optional[str]]:
    """Net tangible assets ($mm) from the asset snapshot ONLY (no forensic fallback):
    total assets − intangibles incl. goodwill."""
    snap = ov.get("asset_snapshot") or {}
    total = (snap.get("total_assets") or {}).get("value")
    if total is None:
        return None, None
    intang = (snap.get("intangibles") or {}).get("value") or 0.0
    nta = (total - intang) / 1e6
    return nta, (f"total assets ${total / 1e6:,.0f}M − intangibles incl. goodwill "
                 f"${intang / 1e6:,.0f}M")


def build_liens_headroom(ov: dict) -> dict:
    """Permitted-liens headroom archetypes per extracted lien fact, the fixed unbounded
    gate (unsecured/convertible notes whose governing family was extracted with no lien
    fact — the LCID pattern; never fires vacuously on AAL), LME chips, and the priming
    pre-seed for the Recovery page."""
    covs = ov.get("covenants") or []
    if not covs:
        return {"available": False,
                "note": "no covenant packages extracted — permitted-liens read "
                        "unavailable (re-run the pipeline with the LLM on to extract)"}

    nta, nta_formula = _nta_mm(ov)
    rows: list[dict] = []
    for pkg in covs:
        fam = pkg.get("family_label") or pkg.get("agreement_type") or "agreement"
        for f in _lien_facts(pkg):
            tok = parse_capacity_tokens(f.get("value"))
            headroom = None
            if f["_ratio_cov"] or _RATIO_COV_RE.search(f.get("value") or ""):
                arch = "ratio_only"
                detail = "headroom requires a collateral appraisal — not extracted"
            elif tok["pct"] is not None and _NTA_RE.search(f.get("value") or "") \
                    and nta is not None:
                arch = "computed"
                v = round(tok["pct"] * nta, 1)
                headroom = derived_value(
                    v, f"{100 * tok['pct']:.0f}% × net tangible assets ({nta_formula})",
                    f"${v:,.0f}M").model_dump()
                detail = None
            elif tok["dollars"] is not None:
                arch = "stated_capacity"
                detail = "stated capacity (utilization unknown) — never read as headroom"
            else:
                arch = "present_unquantified"
                detail = ("lien covenant present — capacity not quantified in the "
                          "extraction window")
            rows.append({"family": fam, "name": f.get("name"), "value": f.get("value"),
                         "quote": f.get("quote"), "archetype": arch, "detail": detail,
                         "dollars_mm": tok["dollars"], "pct": tok["pct"],
                         "ratio": tok["ratio"], "headroom": headroom})

    # unbounded gate (fixed): unsecured/convertible notes exist AND a family governing
    # them was extracted AND that family has no lien fact
    unbounded: list[str] = []
    for inst in ov.get("debt_schedule") or []:
        if not (inst.get("secured") is False or inst.get("seniority") == "convertible"):
            continue
        name = inst.get("instrument") or ""
        governing = [p for p in covs if name in (p.get("governs_instruments") or [])]
        if governing and not any(_lien_facts(p) for p in governing):
            unbounded.append(name)

    if unbounded:
        archetype = "unbounded"
    elif any(r["archetype"] == "computed" for r in rows):
        archetype = "computed"
    elif any(r["archetype"] == "ratio_only" for r in rows):
        archetype = "ratio_only"
    elif any(r["archetype"] == "stated_capacity" for r in rows):
        archetype = "stated_capacity"
    elif rows:
        archetype = "present_unquantified"
    else:
        archetype = "none_extracted"

    uptier = None
    for pkg in covs:
        for v in pkg.get("lme_vectors") or []:
            if v.get("vector") == "uptier_priming" and \
                    v.get("risk") not in (None, "not_addressed"):
                uptier = {"risk": v.get("risk"), "rationale": v.get("rationale"),
                          "family": pkg.get("family_label") or pkg.get("agreement_type")}
                break
        if uptier:
            break
    jc = [pkg.get("j_crew_blocker_present") for pkg in covs]
    j_crew = (True if any(v is True for v in jc)
              else False if any(v is False for v in jc) else None)

    suggested = None
    computed = [r for r in rows if r["headroom"]]
    if computed:
        best = max(computed, key=lambda r: r["headroom"]["value"] or 0.0)
        suggested = {"value": best["headroom"]["value"],
                     "basis": f"computed NTA headroom — {best['name']}",
                     "note": best["headroom"]["formula"]}
    else:
        dollar = [r for r in rows if r["dollars_mm"]]
        if dollar:
            best = max(dollar, key=lambda r: r["dollars_mm"])
            suggested = {"value": best["dollars_mm"],
                         "basis": f"largest $-token on a lien fact — {best['name']} "
                                  "(stated capacity, utilization unknown)",
                         "note": (best["value"] or "")[:160]}

    return {
        "available": True,
        "archetype": archetype,
        "unbounded_instruments": unbounded,
        "unbounded_note": (
            "no lien covenant extracted in the famil(ies) governing "
            + ", ".join(unbounded[:4])
            + " — unbounded priming risk (covenant-lite; Moyer ch. 9)"
            if unbounded else None),
        "rows": rows,
        "uptier_priming": uptier,
        "j_crew_blocker_present": j_crew,
        "suggested_priming": suggested,
        "derivation": "deterministic $ / % / ratio tokens over cached covenant "
                      "extractions; NTA headroom = pct × (total assets − intangibles "
                      "incl. goodwill) from the asset snapshot; absolute-$ baskets are "
                      "stated capacity, never headroom (Moyer ch. 9)",
    }
