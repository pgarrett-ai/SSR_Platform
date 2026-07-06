"""Orchestration: simulate -> waterfall -> recovery distribution -> fulcrum.

The headline output is a recovery distribution for each tranche (as a percent of
face) and the implied **fulcrum security**: the most-senior class that is not
made whole - the paper that absorbs the loss at the margin and therefore the one
that typically converts into the reorganized equity. If you believe in the
reorg, that is usually where you want to own the debt.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .simulate import SimConfig, SimOutput, simulate_enterprise_value
from .structure import CapitalStructure
from .waterfall import run_waterfall


@dataclass
class RecoveryResult:
    structure: CapitalStructure
    sim: SimOutput
    recoveries: dict[str, np.ndarray]   # tranche name -> (N,) recovery $
    fulcrum: str | None
    accrual_years: float = 0.0          # accrued-interest assumption used for the claims

    # -- per-tranche statistics -------------------------------------------
    def table(self) -> pd.DataFrame:
        """One row per tranche, most-senior first, with recovery statistics.

        Recovery % is measured against the allowed **claim** (principal + accrued + make-whole),
        so a class paid its full claim shows 100% and 'made whole' means recovering accrued too.
        """
        order = self.structure.priority_order()
        tmap = {t.name: t for t in self.structure.tranches}
        ent = {t.name: t.entity for t in self.structure.tranches}

        rows = []
        for name in order:
            rec = self.recoveries[name]
            t = tmap[name]
            claim = t.claim(self.accrual_years)
            pct = rec / claim if claim > 0 else np.zeros_like(rec)
            rows.append(
                {
                    "tranche": name,
                    "entity": ent[name],
                    "face": t.face,
                    "accrued_$": t.accrued(self.accrual_years),
                    "make_whole_$": t.make_whole,
                    "claim": claim,
                    "mean_recovery_%": 100 * pct.mean(),
                    "mean_recovery_$": rec.mean(),
                    "median_recovery_%": 100 * np.median(pct),
                    "p10_%": 100 * np.percentile(pct, 10),
                    "p90_%": 100 * np.percentile(pct, 90),
                    "lgd_%": 100 * (1.0 - pct.mean()),          # loss given default
                    "prob_impaired_%": 100 * (pct < 0.999).mean(),
                    "prob_full_%": 100 * (pct >= 0.999).mean(),
                    "prob_zero_%": 100 * (pct <= 0.001).mean(),
                    "is_fulcrum": name == self.fulcrum,
                }
            )
        return pd.DataFrame(rows)

    def summary(self) -> str:
        df = self.table()
        ev = self.sim.ev
        lines = [
            f"Capital structure: {self.structure.name}",
            f"Draws: {len(ev):,}   |   Admin fees: {self.structure.admin_fees:,.0f}",
            f"Enterprise value  median {np.median(ev):,.0f}   "
            f"P10 {np.percentile(ev, 10):,.0f}   P90 {np.percentile(ev, 90):,.0f}",
            f"Total face: {self.structure.total_face():,.0f}",
            f"Fulcrum security: {self.fulcrum or 'none (all classes impaired or all whole)'}",
            "",
            df.to_string(
                index=False,
                float_format=lambda x: f"{x:,.1f}",
            ),
        ]
        return "\n".join(lines)


def _find_fulcrum(structure: CapitalStructure, recoveries: dict[str, np.ndarray],
                  accrual_years: float = 0.0) -> str | None:
    """Most-senior tranche whose median recovery is below its full claim.

    Walking top-down, the first class that is not made whole (principal + accrued +
    make-whole) in the median scenario is the fulcrum.
    """
    for name in structure.priority_order():
        t = next(t for t in structure.tranches if t.name == name)
        claim = t.claim(accrual_years)
        if claim <= 0:
            continue
        median_pct = np.median(recoveries[name] / claim)
        if median_pct < 0.999:
            return name
    return None  # every class is made whole in the median case


def analyze(structure: CapitalStructure, cfg: SimConfig) -> RecoveryResult:
    """End-to-end: simulate EV, run the waterfall, summarize recoveries."""
    sim = simulate_enterprise_value(cfg)
    recoveries = run_waterfall(structure, sim.ev, cfg.accrual_years)
    fulcrum = _find_fulcrum(structure, recoveries, cfg.accrual_years)
    return RecoveryResult(structure=structure, sim=sim, recoveries=recoveries,
                          fulcrum=fulcrum, accrual_years=cfg.accrual_years)
