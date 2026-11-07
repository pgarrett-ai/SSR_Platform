"""CapStack overview -> Fulcrum CapitalStructure: the seniority classifier and the
overview mapper. The overview comes from an in-process `run_overview()` call.
"""
from __future__ import annotations

import re
from typing import Optional

from .structure import CapitalStructure, Entity, Tranche


def classify_seniority(seniority: Optional[str], secured: Optional[bool], instrument: str) -> tuple[bool, int, bool]:
    """Map CapStack's free-text seniority to (secured, lien_rank, preferred). Super-priority
    and DIP facilities, and receivables securitizations (the receivables sit in a
    bankruptcy-remote vehicle), rank 0 ahead of every lien. An explicit unsecured flag beats
    the name patterns: 'Senior unsecured revolving credit facility' is not a first lien."""
    text = f"{seniority or ''} {instrument}".lower()
    if "preferred" in text:
        return False, 99, True
    if re.search(r"super[- ]?priority|debtor[- ]in[- ]possession|\bdip\b|securiti[sz]ation", text):
        return True, 0, False
    if secured is False or "unsecured" in (seniority or "").lower():
        return False, 99, False
    if re.search(r"third[- ]lien|3l\b", text):
        return True, 3, False
    if re.search(r"second[- ]lien|2l\b|junior[- ]lien", text):
        return True, 2, False
    if secured or re.search(r"first[- ]lien|1l\b|senior secured|term loan|revolv|credit facilit|equipment note|eetc|mortgage", text):
        return True, 1, False
    return False, 99, False  # senior unsecured / subordinated / unknown


def overview_to_structure(overview: dict) -> tuple[CapitalStructure, Optional[float], dict]:
    """Build a single-entity CapitalStructure ($mm) from a CapStack overview.

    CapStack's debt schedule has no legal-entity mapping, so all tranches sit at one
    OpCo; the UI lets the user split HoldCo/OpCo manually.
    Returns (structure, ltm_ebitda_mm, citations) - EBITDA may be None; citations maps
    tranche name -> the filing citation behind its face amount (drill-down provenance).
    """
    name = (overview.get("header") or {}).get("issuer") or "Company"
    tranches: list[Tranche] = []
    citations: dict[str, dict] = {}
    seen: set[str] = set()

    for i, inst in enumerate(overview.get("debt_schedule") or []):
        amount, citation = _claim_amount(inst)
        if not amount or amount <= 0:
            continue
        secured, lien, preferred = classify_seniority(
            inst.get("seniority"), inst.get("secured"), inst.get("instrument", "")
        )
        tname = inst.get("instrument") or f"Tranche {i + 1}"
        while tname in seen:
            tname += " *"
        seen.add(tname)
        if citation:
            citations[tname[:80]] = citation
        tranches.append(
            Tranche(
                name=tname[:80],
                entity="OpCo",
                face=amount / 1e6,
                lien_rank=lien,
                secured=secured,
                preferred=preferred,
                coupon=_tranche_coupon(inst),
                maturity=inst.get("maturity"),
            )
        )

    structure = CapitalStructure(
        name=name,
        entities=[Entity("OpCo", ev_share=1.0, parent=None)],
        tranches=tranches,
        admin_fees=0.0,
    )
    return structure, ltm_ebitda(overview)[0], citations


_FACE_TOLERANCE = 0.10   # principal within this share of carrying is the same debt


def _claim_amount(inst: dict) -> tuple[Optional[float], Optional[dict]]:
    """($, citation) a drawn instrument claims. Notes and term loans claim principal: the
    tagged face less any tagged OID (unmatured interest, not a claim). Carrying amounts net
    issuance costs (LCID 2030 converts: $1,085M carrying, $1,100M principal) or add an
    exchange premium (TSE 2029 PIK notes: $419.9M carrying, $391.0M par, which already
    includes the PIK), and neither changes the claim. A principal more than 10% off
    carrying (an EETC tagged at its $339M issuance face, $228M still owed) claims the
    carrying amount. A revolver's face tag is a sub-limit or the facility size (TSE's
    super-priority revolver: $50M tag, $265M drawn), so revolvers and untagged instruments
    claim the carrying amount."""
    cv = next((inst[k] for k in ("outstanding", "principal")
               if (inst.get(k) or {}).get("value")), None)
    if cv is None:
        return None, None
    carrying = float(cv["value"]), cv.get("citation") or inst.get("citation")
    face = inst.get("face_amount") or {}
    if not face.get("value") or inst.get("facility_type") not in ("notes", "term loan"):
        return carrying
    oid = (inst.get("unamortized_discount") or {}).get("value") or 0.0
    principal = float(face["value"]) - float(oid)
    # ponytail: a 10% band, not a reconciliation. The face tag is par at issuance; further
    # off carrying it is stale (amortized, repurchased or exchanged paper) and carrying claims
    if abs(principal - carrying[0]) > _FACE_TOLERANCE * carrying[0]:
        return carrying
    return principal, face.get("citation")


def ltm_ebitda(overview: dict) -> tuple[Optional[float], Optional[str]]:
    """($mm, label) of the canonical EBITDA: the forensic column forensic.ltm_ebitda picks
    (the latest one with an EBITDA), so Recovery seeds from the number the leverage ratios
    use. Read off the serialized rows; a row without a fiscal year is malformed and skipped.
    The label names its definition and period."""
    for row in reversed(overview.get("forensic_table") or []):
        e = row.get("ebitda") or {}
        if row.get("fiscal_year") is not None and e.get("value") is not None:
            return e["value"] / 1e6, e.get("note") or e.get("formula")
    return None, None


def _tranche_coupon(inst: dict) -> float:
    """Accrual coupon for a tranche. Tagged XBRL rates first — the effective floater rate,
    then the stated coupon (range midpoint) — so 'SOFR + 2.75%' is never read as 2.75%."""
    eff = inst.get("effective_rate_pct")
    if eff:
        return float(eff) / 100.0
    cp = inst.get("coupon_pct")
    if cp:
        hi = inst.get("coupon_pct_max")
        return (float(cp) + float(hi)) / 2.0 / 100.0 if hi else float(cp) / 100.0
    return _parse_coupon(inst.get("coupon"))


def _parse_coupon(coupon: Optional[str]) -> float:
    """String fallback: the LAST percent wins — 'SOFR + 2.75% → 6.05%' -> 0.0605, and
    'rates ranging from 2.88% to 7.15%, averaging 3.95%' -> 0.0395. Unparseable -> 0.0."""
    if not coupon:
        return 0.0
    hits = re.findall(r"(\d+(?:\.\d+)?)\s*%", coupon)
    return float(hits[-1]) / 100.0 if hits else 0.0
