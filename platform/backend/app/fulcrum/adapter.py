"""CapStack overview -> Fulcrum CapitalStructure.

Kept from fulcrum's capstack_bridge.py: the seniority classifier and the overview mapper.
Deleted from it: the three-tier loader (live API -> disk-cache path hack -> XBRL seed) —
in the merged platform the overview comes from an in-process `run_overview()` call.
"""
from __future__ import annotations

import re
from typing import Optional

from .structure import CapitalStructure, Entity, Tranche


def classify_seniority(seniority: Optional[str], secured: Optional[bool], instrument: str) -> tuple[bool, int, bool]:
    """Map CapStack's free-text seniority to (secured, lien_rank, preferred)."""
    text = f"{seniority or ''} {instrument}".lower()
    if "preferred" in text:
        return False, 99, True
    if re.search(r"third[- ]lien|3l\b", text):
        return True, 3, False
    if re.search(r"second[- ]lien|2l\b|junior[- ]lien", text):
        return True, 2, False
    if secured or re.search(r"first[- ]lien|1l\b|senior secured|term loan|revolv|credit facilit|equipment note|eetc|mortgage", text):
        return True, 1, False
    return False, 99, False  # senior unsecured / subordinated / unknown


def overview_to_structure(overview: dict) -> tuple[CapitalStructure, Optional[float], dict]:
    """Build a single-entity CapitalStructure ($mm) from a CapStack overview.

    CapStack's debt schedule has no legal-entity mapping yet (Phase 4 item), so all
    tranches sit at one OpCo; the UI lets the user split HoldCo/OpCo manually.
    Returns (structure, latest_ebitda_mm, citations) - EBITDA may be None; citations maps
    tranche name -> the filing citation behind its face amount (drill-down provenance).
    """
    name = (overview.get("header") or {}).get("issuer") or "Company"
    tranches: list[Tranche] = []
    citations: dict[str, dict] = {}
    seen: set[str] = set()

    for i, inst in enumerate(overview.get("debt_schedule") or []):
        amount, citation = None, None
        for key in ("outstanding", "principal"):
            cv = inst.get(key)
            if cv and cv.get("value"):
                amount = float(cv["value"])
                citation = cv.get("citation") or inst.get("citation")
                break
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

    ebitda = None
    for row in reversed(overview.get("forensic_table") or []):
        cv = row.get("ebitda")
        if cv and cv.get("value"):
            ebitda = float(cv["value"]) / 1e6
            break
    return structure, ebitda, citations


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
