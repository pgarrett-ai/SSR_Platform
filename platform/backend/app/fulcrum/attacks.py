"""Priority-attack scenarios (Moyer ch. 12): never take the stated priority stack as
fixed. Each attack is a pure transform of the CapitalStructure; the caller re-runs the
waterfall on the SAME EV draws and renders the payout matrix side by side with base.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Optional

from .structure import CapitalStructure, Entity, Tranche

ATTACKS = ("lien_avoidance", "equitable_subordination", "substantive_consolidation")


def apply_attack(structure: CapitalStructure, kind: str,
                 target: Optional[str] = None) -> CapitalStructure:
    """Return a transformed copy. `target` = tranche name (default: every secured tranche
    for the secured-status attacks)."""
    if kind == "guarantee_invalidation":
        raise ValueError("guarantee edges are not modeled (tranches sit at one entity) — "
                         "descoped; re-home the tranche manually to simulate it")
    if kind not in ATTACKS:
        raise ValueError(f"unknown attack '{kind}' — one of {ATTACKS}")

    def hit(t: Tranche) -> bool:
        return t.name == target if target else t.secured

    if kind == "lien_avoidance":
        # perfection defect / voidable preference: secured claim becomes general unsecured
        tranches = [replace(t, secured=False) if hit(t) else replace(t) for t in structure.tranches]
        entities = [replace(e) for e in structure.entities]
    elif kind == "equitable_subordination":
        # lender misconduct: the claim drops BELOW the unsecured pool. preferred=True pays
        # after all debt, before equity — exactly that slot (reuse, not a new engine tier).
        tranches = [replace(t, secured=False, preferred=True) if hit(t) else replace(t)
                    for t in structure.tranches]
        entities = [replace(e) for e in structure.entities]
    else:  # substantive_consolidation: entity silos merge; structural subordination vanishes
        entities = [Entity("Consolidated", ev_share=1.0, parent=None)]
        tranches = [replace(t, entity="Consolidated") for t in structure.tranches]

    return CapitalStructure(name=f"{structure.name} · {kind}", entities=entities,
                            tranches=tranches, admin_fees=structure.admin_fees,
                            admin_pct=structure.admin_pct)
