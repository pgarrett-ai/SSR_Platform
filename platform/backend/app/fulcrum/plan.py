"""Plan-of-reorganization recovery & ROI (Moyer ch. 12-13).

A confirmed/proposed plan is an **exogenous** distribution: the analyst types what
each class receives (cash, new debt, new equity, warrants, subscription rights).
We value that package per class, divide by the allowed claim for recovery %, and
annualize it against the entry (market) price over the case duration. The
waterfall is only the *comparison baseline* (what an absolute-priority
distribution at the same reorg EV would pay a class) — never the plan itself, so
we resist re-simulating the plan through `run_waterfall` (Moyer: plan value is
negotiated, not APR-mechanical).

Conventions (matching proforma.py sibling): all $ in $mm; percentages returned as
decimals-times-100 (e.g. 90.0 = 90%). Every displayed number carries a
`derived`-formula spine via `_cv`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .structure import CapitalStructure
from .waterfall import run_waterfall

# Moyer ch. 12: average time in chapter 11 ≈ 14 months. Used only as the ROI
# horizon fallback when the analyst supplies no duration.
DEFAULT_DURATION_YEARS = 14.0 / 12.0


@dataclass
class PlanConsideration:
    """What one class receives under the plan of reorganization.

    new_debt_haircut: market value as a fraction of new-debt face. Post-reorg debt
        trades at a discount (Moyer ch. 13) — there is deliberately NO par default;
        it must be supplied whenever new debt is granted, or evaluate_plan raises.
    new_equity_pct: this class's share of reorg equity value, in percent (0..100).
    warrant_value: analyst estimate of warrant value ($mm) — v1 has no option model.
    rights_shares / rights_strike: subscription rights valued at intrinsic worth,
        max(0, per-share reorg equity value − strike) × shares (needs reorg_shares).
    """

    tranche: str
    cash: float = 0.0
    new_debt_face: float = 0.0
    new_debt_haircut: Optional[float] = None
    new_equity_pct: float = 0.0
    warrant_value: float = 0.0
    rights_shares: float = 0.0
    rights_strike: float = 0.0


def _cv(value: Optional[float], display: str, formula: str,
        note: Optional[str] = None, unit: str = "USD") -> dict:
    """A CitedValue-shaped derived number (frontend CitedNumber reads display/derived/formula/note)."""
    return {"value": value, "display": display, "unit": unit,
            "derived": True, "formula": formula, "note": note}


def evaluate_plan(structure: CapitalStructure, plan: list[PlanConsideration], *,
                  reorg_ev: float, reorg_debt: float, reorg_shares: Optional[float] = None,
                  accrual_years: float = 0.0, entry_prices: Optional[dict] = None,
                  duration_years: Optional[float] = None) -> dict:
    """Value each class's plan package → recovery % of allowed claim → annualized ROI,
    with a per-class delta vs the absolute-priority recovery at the same reorg EV.

    reorg_ev / reorg_debt: plan enterprise value and post-reorg debt ($mm); their
        difference is the reorg equity value that the new-equity / rights legs draw on
        (Moyer ch. 13). Echoed back so F5/F6 render the identical figure.
    entry_prices: {tranche_name: price per 100 of face} (from match_quotes). ROI is
        computed only where a price is present, else None ("unquoted").
    """
    reorg_equity_value = max(reorg_ev - reorg_debt, 0.0)
    per_share = (reorg_equity_value / reorg_shares) if (reorg_shares and reorg_shares > 0) else None
    dur = duration_years if (duration_years and duration_years > 0) else DEFAULT_DURATION_YEARS
    entry_prices = entry_prices or {}
    tmap = {t.name: t for t in structure.tranches}

    # comparison baseline: absolute-priority recovery at the same reorg EV (one waterfall run)
    wf = run_waterfall(structure, np.asarray([float(reorg_ev)], dtype=float),
                       accrual_years=accrual_years)

    rows = []
    for cons in plan:
        t = tmap.get(cons.tranche)
        if t is None:
            raise ValueError(f"unknown plan target '{cons.tranche}'")
        if cons.new_debt_face and cons.new_debt_haircut is None:
            raise ValueError(
                f"{cons.tranche}: new_debt_haircut required when new_debt_face > 0 "
                "(post-reorg debt trades at a discount — no par default)")

        claim = t.claim(accrual_years)
        face = t.face
        haircut = cons.new_debt_haircut if cons.new_debt_haircut is not None else 0.0
        new_debt_value = cons.new_debt_face * haircut
        equity_value = cons.new_equity_pct / 100.0 * reorg_equity_value
        rights_value = (max(0.0, per_share - cons.rights_strike) * cons.rights_shares
                        if (per_share is not None and cons.rights_shares > 0) else 0.0)
        plan_value = (cons.cash + new_debt_value + equity_value
                      + cons.warrant_value + rights_value)

        recovery_pct = 100.0 * plan_value / claim if claim > 0 else 0.0
        recovery_per_100 = 100.0 * plan_value / face if face > 0 else None

        entry = entry_prices.get(cons.tranche)
        roi = None
        # guard against a negative package -> fractional power of a negative -> complex;
        # a zero package annualizes to -100% naturally (0 ** (1/dur) == 0).
        if recovery_per_100 is not None and recovery_per_100 >= 0 and entry and entry > 0:
            roi = (recovery_per_100 / entry) ** (1.0 / dur) - 1.0

        market_recovery = float(wf[cons.tranche][0]) if cons.tranche in wf else 0.0
        market_pct = 100.0 * market_recovery / claim if claim > 0 else 0.0

        rights_note = (None if per_share is not None or cons.rights_shares == 0
                       else "rights not valued — no reorg share count supplied")
        rows.append({
            "tranche": cons.tranche,
            "face": face,
            "claim": _cv(claim, f"${claim:,.1f}M",
                         f"face ${face:,.1f}M + accrued + make-whole (accrual {accrual_years:.2f}y)"),
            "plan_value": _cv(plan_value, f"${plan_value:,.1f}M",
                              f"cash ${cons.cash:,.1f}M + new debt ${cons.new_debt_face:,.1f}M×"
                              f"{haircut:.2f} + equity {cons.new_equity_pct:.1f}%×"
                              f"${reorg_equity_value:,.1f}M"
                              + (f" + warrants ${cons.warrant_value:,.1f}M" if cons.warrant_value else "")
                              + (f" + rights ${rights_value:,.1f}M" if rights_value else ""),
                              note=rights_note),
            "recovery_pct": _cv(round(recovery_pct, 1), f"{recovery_pct:,.1f}%",
                                f"plan value ${plan_value:,.1f}M ÷ allowed claim ${claim:,.1f}M",
                                unit="%"),
            "recovery_per_100": (None if recovery_per_100 is None else
                                 _cv(round(recovery_per_100, 2), f"{recovery_per_100:,.2f}",
                                     f"plan value ${plan_value:,.1f}M ÷ face ${face:,.1f}M × 100 "
                                     "(per 100 of face, comparable to the market price)")),
            "roi": (None if roi is None else
                    _cv(round(100.0 * roi, 1), f"{100.0 * roi:,.1f}%",
                        f"(plan {recovery_per_100:,.2f} ÷ entry {entry:,.2f})^(1/{dur:.2f}) − 1 "
                        "— annualized over the case duration", unit="%")),
            "market_pct": _cv(round(market_pct, 1), f"{market_pct:,.1f}%",
                              f"absolute-priority recovery ${market_recovery:,.1f}M ÷ claim "
                              f"${claim:,.1f}M at reorg EV ${reorg_ev:,.1f}M", unit="%"),
            "delta_pct": _cv(round(recovery_pct - market_pct, 1),
                             f"{recovery_pct - market_pct:+,.1f}%",
                             "plan recovery − absolute-priority recovery at the same reorg EV "
                             "(positive = plan pays this class above APR)", unit="%"),
        })

    return {
        "available": True,
        "reorg_equity_value": _cv(round(reorg_equity_value, 1), f"${reorg_equity_value:,.1f}M",
                                  f"plan EV ${reorg_ev:,.1f}M − post-reorg debt ${reorg_debt:,.1f}M "
                                  "(Moyer ch. 13)"),
        "duration_years": dur,
        "entry_source": "matched drop-file quote (per 100 of face)" if entry_prices else "unquoted",
        "rows": rows,
    }
