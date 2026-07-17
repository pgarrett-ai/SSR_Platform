"""Vectorized absolute-priority waterfall.

Everything operates on arrays of shape (N,) - one element per Monte Carlo draw -
so a full run is a handful of vectorized numpy operations, not a Python loop over
scenarios.

Per entity, given a distributable value V (already net of upstream equity):

    1. secured tranches, paid by lien rank, sharing V (all-asset pledge).
       Tranches at the same lien rank are pari passu and share pro rata. A
       secured tranche short of its face generates an unsecured *deficiency*
       claim for the shortfall.
    2. unsecured pool: general unsecured tranches + secured deficiencies, paid
       pro rata out of whatever value remains.
    3. preferred stock, pro rata, after all debt.
    4. residual = common equity, which upstreams to the parent entity.

Because senior secured paper is paid before junior secured paper out of the same
pool, the economic effect of an intercreditor *turnover* provision is already
enforced (a junior lien cannot be paid while a senior lien from the same
collateral is short). See README for the separate-collateral-pool case.
"""

from __future__ import annotations

import numpy as np

from .structure import CapitalStructure, Tranche


def allocate_entity(
    value: np.ndarray,
    tranches: list[Tranche],
    fees: np.ndarray | float = 0.0,
    accrual_years: float = 0.0,
) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Allocate one entity's value across its tranches under absolute priority.

    Parameters
    ----------
    value : (N,) distributable value at this entity (operations + upstreamed equity).
    tranches : the claims sitting at this entity.
    fees : entity-level admin/fee claim, paid ahead of all tranches.

    Returns
    -------
    recoveries : dict of tranche name -> (N,) recovery in dollars.
    equity : (N,) residual value flowing up to the parent.
    """
    value = np.asarray(value, dtype=float)
    remaining = np.maximum(value - fees, 0.0)
    recoveries: dict[str, np.ndarray] = {}

    def _pay_pro_rata(claims: list[tuple[str, np.ndarray]]) -> None:
        """Pay a pari passu pool out of `remaining`, pro rata by claim size."""
        nonlocal remaining
        if not claims:
            return
        total_claim = np.sum([c for _, c in claims], axis=0)
        pay_total = np.minimum(remaining, total_claim)
        with np.errstate(divide="ignore", invalid="ignore"):
            frac = np.where(total_claim > 0, pay_total / total_claim, 0.0)
        for name, c in claims:
            recoveries[name] = recoveries.get(name, np.zeros_like(value)) + frac * c
        remaining = remaining - pay_total

    # Claim = principal + accrued interest + make-whole (the allowed claim, not just face).
    def _claim(t: Tranche) -> float:
        return t.claim(accrual_years)

    # 1. Secured, by lien rank; tranches sharing a rank are pari passu (pro rata).
    #    A collateral_value caps the SECURED claim (§506); the shortfall vs the full
    #    allowed claim becomes an unsecured deficiency either way.
    secured = [t for t in tranches if t.secured]
    deficiency: list[tuple[str, np.ndarray]] = []
    for rank in sorted({t.lien_rank for t in secured}):
        group = [t for t in secured if t.lien_rank == rank]
        _pay_pro_rata([(t.name, np.full_like(value, min(_claim(t), t.collateral_value)
                                             if t.collateral_value is not None else _claim(t)))
                       for t in group])
        for t in group:
            shortfall = _claim(t) - recoveries[t.name]  # deficiency, pari with unsecured
            deficiency.append((t.name, shortfall))

    # 2. Unsecured pool: general unsecured claims + secured deficiencies, pro rata.
    unsecured_claims: list[tuple[str, np.ndarray]] = []
    for t in tranches:
        if not t.secured and not t.preferred:
            recoveries.setdefault(t.name, np.zeros_like(value))
            unsecured_claims.append((t.name, np.full_like(value, _claim(t))))
    _pay_pro_rata(unsecured_claims + deficiency)

    # 2b. Contractual subordination (Moyer ch. 7 subrogation): redirect a subordinated
    # tranche's recovery to its benefited tranche until that claim is paid in full.
    # ponytail: edges processed in tranche-list order; chained subordination (A->B->C)
    # would need a topological pass nobody has modeled yet.
    for t in tranches:
        if t.subordinated_to is not None and t.name in recoveries:
            target = next(x for x in tranches if x.name == t.subordinated_to)
            recoveries.setdefault(target.name, np.zeros_like(value))
            shortfall = np.maximum(_claim(target) - recoveries[target.name], 0.0)
            transfer = np.minimum(recoveries[t.name], shortfall)
            recoveries[t.name] = recoveries[t.name] - transfer
            recoveries[target.name] = recoveries[target.name] + transfer

    # 3. Preferred stock: after all debt, before common equity.
    preferred_claims = []
    for t in tranches:
        if t.preferred:
            recoveries.setdefault(t.name, np.zeros_like(value))
            preferred_claims.append((t.name, np.full_like(value, _claim(t))))
    _pay_pro_rata(preferred_claims)

    equity = np.maximum(remaining, 0.0)
    return recoveries, equity


def run_waterfall(structure: CapitalStructure, ev: np.ndarray,
                  accrual_years: float = 0.0) -> dict[str, np.ndarray]:
    """Run the full structural waterfall over a vector of enterprise values.

    Admin fees are treated as the most-senior estate cost and removed from
    enterprise value before it is split across entities. Each entity's value is
    its share of the (net) EV plus residual equity upstreamed from its children;
    entities are resolved children-first.
    """
    ev = np.asarray(ev, dtype=float)
    ev_net = np.maximum(ev * (1.0 - structure.admin_pct) - structure.admin_fees, 0.0)

    emap = structure.entity_map()
    upstream: dict[str, np.ndarray] = {e.name: np.zeros_like(ev_net) for e in structure.entities}
    recoveries: dict[str, np.ndarray] = {}

    for ent_name in structure.post_order():  # children before parents
        ent = emap[ent_name]
        own_value = ev_net * ent.ev_share
        value = own_value + upstream[ent_name]

        recs, equity = allocate_entity(value, structure.tranches_at(ent_name),
                                       accrual_years=accrual_years)
        recoveries.update(recs)

        if ent.parent is not None:
            upstream[ent.parent] = upstream[ent.parent] + equity

    return recoveries
