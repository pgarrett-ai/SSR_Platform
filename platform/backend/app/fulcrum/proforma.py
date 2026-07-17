"""Pro-forma capital-structure transforms (Moyer ch. 9/11): the priming layer that
permitted-lien capacity (or a covenant-lite gap) allows, and the exchange-offer
transform (stub + new paper + holdout/tender payoffs). attacks.py discipline: pure
transforms over fresh copies; the caller re-runs the waterfall on the SAME EV draws
(or EV grid).
"""
from __future__ import annotations

from dataclasses import replace
from typing import Optional

import numpy as np

from .structure import UNSECURED, CapitalStructure, Tranche
from .waterfall import run_waterfall


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


# --------------------------------------------------------------------------- #
# F4: exchange offer (Moyer ch. 11)
# --------------------------------------------------------------------------- #

_EXCHANGE_SENIORITIES = ("priming", "second_lien", "unsecured")


def exchange(structure: CapitalStructure, target: str, *, ratio_pct: float,
             participation_pct: float, seniority: str, coupon: float = 0.0,
             exit_consent: bool = False) -> CapitalStructure:
    """Exchange-offer transform (Moyer ch. 11): participation p of `target` tenders into
    a new tranche at face = F × p × ratio/100; the stub keeps F × (1−p). Seniority:
    'priming' = rank below the lowest secured rank (rank 0 actually primes — pari would
    share pro rata), 'second_lien' = max secured rank + 1, or 'unsecured'.
    `exit_consent` contractually subordinates the stub to the new paper (single-hop,
    same-entity). Maturity-based coercion is not modeled (Tranche.maturity is
    informational). p=0 skips the new tranche (≡ base); p=1 skips the stub.
    Validators re-run on construction — the API's 400 boundary."""
    tmap = {t.name: t for t in structure.tranches}
    if target not in tmap:
        raise ValueError(f"unknown exchange target '{target}'")
    if seniority not in _EXCHANGE_SENIORITIES:
        raise ValueError(f"seniority must be one of {_EXCHANGE_SENIORITIES}")
    if not 0.0 <= participation_pct <= 100.0:
        raise ValueError("participation_pct must be in [0, 100]")
    if ratio_pct < 0:
        raise ValueError("ratio_pct must be non-negative")

    t0 = tmap[target]
    p = participation_pct / 100.0
    new_face = t0.face * p * ratio_pct / 100.0
    new_name = f"{target} · exchange"
    has_new = new_face > 0
    secured_ranks = [t.lien_rank for t in structure.tranches if t.secured]
    if seniority == "priming":
        sec, rank = True, (min(secured_ranks) - 1 if secured_ranks else 0)
    elif seniority == "second_lien":
        sec, rank = True, (max(secured_ranks) + 1 if secured_ranks else 1)
    else:
        sec, rank = False, UNSECURED

    tranches: list[Tranche] = []
    for t in structure.tranches:
        if t.name != target:
            tranches.append(replace(t))
            continue
        if p < 1.0:
            tranches.append(replace(
                t, face=t.face * (1.0 - p),
                subordinated_to=new_name if (exit_consent and has_new)
                else t.subordinated_to))
        if has_new:
            tranches.append(Tranche(name=new_name, entity=t.entity, face=new_face,
                                    lien_rank=rank, secured=sec, coupon=coupon))
    return CapitalStructure(name=f"{structure.name} · exchange",
                            entities=[replace(e) for e in structure.entities],
                            tranches=tranches, admin_fees=structure.admin_fees,
                            admin_pct=structure.admin_pct)


def exchange_scenario(structure: CapitalStructure, target: str, ev,
                      *, ratio_pct: float, participation_pct: float, seniority: str,
                      coupon: float = 0.0, exit_consent: bool = False,
                      cash_per_100: float = 0.0, equity_pct_at_full: float = 0.0,
                      accrual_years: float = 0.0) -> dict:
    """One participation scenario over an EV vector: the exchanged structure, stub /
    new-paper recovery curves (% of allowed claim), the equity residual, and the
    per-100-old-face payoffs (Moyer ch. 11):
        holdout = stub recovery % of its allowed claim;
        tender  = cash/100 + ratio × new-paper recovery % ÷ 100
                  + equity residual × share(p) ÷ (p × F) × 100,
        share(p) = p ÷ (p + (1−e)/e), e = equity_pct_at_full — the tendering pool's
    slice of the equity pot scales sub-linearly with participation (Boxco 36.5).
    Cash consideration is valued at face and does not deplete waterfall EV
    (going-concern convention — the book's cash-depletion point rides the
    holdout-runway chip instead)."""
    ev = np.asarray(ev, dtype=float)
    t0 = next((t for t in structure.tranches if t.name == target), None)
    if t0 is None:
        raise ValueError(f"unknown exchange target '{target}'")
    s2 = exchange(structure, target, ratio_pct=ratio_pct,
                  participation_pct=participation_pct, seniority=seniority,
                  coupon=coupon, exit_consent=exit_consent)
    wf = run_waterfall(s2, ev, accrual_years=accrual_years)
    smap = {t.name: t for t in s2.tranches}
    new_name = f"{target} · exchange"

    def _pct(name: str):
        if name not in wf:
            return None
        c = smap[name].claim(accrual_years)
        return 100.0 * wf[name] / c if c > 0 else np.zeros_like(ev)

    stub_pct = _pct(target)
    new_pct = _pct(new_name)
    ev_net = np.maximum(ev * (1.0 - s2.admin_pct) - s2.admin_fees, 0.0)
    equity = np.maximum(ev_net - sum(wf.values()), 0.0)

    p = participation_pct / 100.0
    e = equity_pct_at_full / 100.0
    tender = None
    if p > 0:
        share = 1.0 if e >= 1.0 else (0.0 if e <= 0.0 else p / (p + (1.0 - e) / e))
        npct = new_pct if new_pct is not None else np.zeros_like(ev)
        equity_leg = equity * share / (p * t0.face) * 100.0 if t0.face > 0 else 0.0
        tender = cash_per_100 + ratio_pct * npct / 100.0 + equity_leg
    return {"structure": s2, "new_name": new_name if new_name in wf else None,
            "stub_pct": stub_pct, "new_pct": new_pct, "equity": equity,
            "tender": tender, "holdout": stub_pct}
