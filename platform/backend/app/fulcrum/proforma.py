"""Pro-forma capital-structure transforms (Moyer ch. 9/11): the priming layer that
permitted-lien capacity (or a covenant-lite gap) allows. attacks.py discipline: pure
transforms over fresh copies; the caller re-runs the waterfall on the SAME EV draws.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Optional

from .structure import CapitalStructure, Tranche


def prime(structure: CapitalStructure, face: float, entity: Optional[str] = None,
          name: str = "Priming loan") -> CapitalStructure:
    """Prepend a rank-0 secured tranche — new money ahead of every existing lien
    (waterfall.py sorts plain int ranks, so 0 pays first; zero engine edits).
    face in $mm; face <= 0 raises ValueError (the API's 400 boundary), as does an
    unknown entity via CapitalStructure.validate."""
    if face is None or face <= 0:
        raise ValueError("priming face must be positive ($mm)")
    tranches = [Tranche(name=name, entity=entity or structure.entities[0].name,
                        face=float(face), lien_rank=0, secured=True),
                *(replace(t) for t in structure.tranches)]
    return CapitalStructure(name=f"{structure.name} · primed",
                            entities=[replace(e) for e in structure.entities],
                            tranches=tranches, admin_fees=structure.admin_fees,
                            admin_pct=structure.admin_pct)
