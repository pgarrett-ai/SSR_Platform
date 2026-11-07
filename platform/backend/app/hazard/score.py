"""Distress scorers behind one interface: score(feats, market) -> dict with the headline
value, zone/PD and availability.

    Altman Z''      accounting-only, every fiscal year and quarter
    Merton DD       market-implied (risk-neutral) DD and first-passage PD term structure
    Trained hazard  the fitted model from train.py, present only when a bundle is saved

References:
  Altman, E. (2005) Z''-score for non-manufacturers / emerging markets.
  Merton, R. (1974); see merton.py.
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Optional

from . import merton as merton_mod
from ..core.config import get_settings


class Scorer:
    name: str = "base"

    def score(self, feats: dict, market=None) -> dict:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Altman Z'' (accounting-only -> works for every fiscal year, no market data)
# --------------------------------------------------------------------------- #
class AltmanZScore(Scorer):
    """Classic four-factor Z'' with its own 1.1 / 2.6 zone cutoffs. No +3.25 constant:
    that belongs to the emerging-market score, whose zones sit 3.25 higher."""
    name = "Altman Z''"
    COEF = {
        "wc_to_assets": 6.56,
        "re_to_assets": 3.26,
        "ebit_to_assets": 6.72,
        "equity_to_liabilities": 1.05,
    }

    def _terms(self, feats: dict) -> Optional[dict]:
        terms = {}
        for k, c in self.COEF.items():
            x = feats.get(k)
            if x is None:
                return None
            terms[k] = c * x
        return terms

    def score(self, feats: dict, market=None) -> dict:
        terms = self._terms(feats)
        if terms is None:
            return {"available": False, "note": "missing Altman inputs"}
        z = sum(terms.values())
        zone = "distress" if z < 1.1 else ("grey" if z < 2.6 else "safe")
        return {"available": True, "value": round(z, 2), "zone": zone}


# --------------------------------------------------------------------------- #
# Merton DD -> PD term structure (needs market equity value + vol)
# --------------------------------------------------------------------------- #
class MertonScorer(Scorer):
    name = "Merton DD"

    def score(self, feats: dict, market=None) -> dict:
        if market is None or not market.ok or market.market_cap is None or market.equity_vol is None:
            return {"available": False, "note": "needs market cap + equity vol"}
        D = feats.get("total_debt")
        if not D or D <= 0:
            return {"available": False, "note": "no debt face value"}
        r = get_settings().risk_free_rate
        res = merton_mod.merton(E=market.market_cap, sigma_E=market.equity_vol, D=D, r=r)
        if res is None:
            return {"available": False, "note": "Merton solve failed"}
        return {
            "available": True,
            "value": round(res.dd_1y, 2),               # distance-to-default (1y)
            "asset_vol": round(res.asset_vol, 3),
            "converged": res.converged,
            "pd": {f"{int(h*12)}m": res.pd_by_horizon[h] for h in (0.25, 0.5, 1.0)},
            "note": "market-implied (risk-neutral) first-passage PD",
            # the exact solved inputs, so cached payloads carry them (refi's conditional
            # PDs re-solve from these instead of reassembling from market/features)
            "inputs": {"E": float(market.market_cap), "sigma_E": float(market.equity_vol),
                       "D": float(D), "r": float(r)},
        }


# --------------------------------------------------------------------------- #
# Trained model (P6) — slots into the same interface; appears only once a model
# has been deliberately trained + saved (so the dashboard stays honest until then).
# --------------------------------------------------------------------------- #
# S&P Global long-run average one-year corporate default rates by rating (1981–2023),
# as fractions. AAA/AA historical rates round to ~0 — floored so log-distance works.
AGENCY_PD_BANDS = [("AAA", 0.0001), ("AA", 0.0002), ("A", 0.0005), ("BBB", 0.0016),
                   ("BB", 0.0063), ("B", 0.0334), ("CCC/C", 0.283)]


def implied_rating(pd12: float) -> str:
    """Nearest agency rating band to a calibrated 12m PD, by log-distance — a cross-check
    against where the agencies actually rate the issuer, not a rating."""
    p = max(pd12, 1e-5)
    return min(AGENCY_PD_BANDS, key=lambda b: abs(math.log(p) - math.log(b[1])))[0]


class TrainedHazardScorer(Scorer):
    """A fitted hazard model behind the Scorer interface (see train.py)."""
    name = "Trained hazard"

    def __init__(self, bundle: dict):
        self.model = bundle["model"]
        self.features = bundle["features"]
        self.label_source = bundle.get("label_source")   # real-label provenance (Phase 5)
        self.walk_forward_auc = bundle.get("walk_forward_auc") or {}
        self.prior = bundle.get("prior")                 # case-control -> real-world rates

    def score(self, feats: dict, market=None) -> dict:
        import numpy as np
        if market is not None and getattr(market, "ok", False):
            # live market snapshot fills the market features the panel trained on
            # (same trailing-window semantics — see market.pit_market_features)
            overlay = {k: getattr(market, k, None)
                       for k in ("equity_vol", "drawdown_52w", "excess_return_1y")}
            feats = {**feats, **{k: v for k, v in overlay.items()
                                 if v is not None and k in self.features}}
        x = np.array([[feats.get(f, np.nan) for f in self.features]], dtype=float)
        if np.isnan(x).all():
            return {"available": False, "note": "no features for trained model"}
        pd12 = float(self.model.predict_proba(x)[0, 1])
        out = {"available": True, "trained": True, "real_labels": bool(self.label_source)}
        if self.prior:
            from .train import prior_correct
            out["uncalibrated"] = round(pd12, 4)
            pd12 = prior_correct(pd12, self.prior["sample_rate"], self.prior["true_rate"])
            out["implied_rating"] = implied_rating(pd12)
        out["value"] = round(pd12, 4)
        if self.label_source:
            aucs = list(self.walk_forward_auc.values())
            out["note"] = self.label_source + (
                f" · walk-forward AUC {min(aucs):.2f}–{max(aucs):.2f}" if aucs else "")
        else:
            out["note"] = "demo-trained (synthetic fixture) — not a real performance claim"
        return out


@lru_cache(maxsize=1)
def _load_trained_bundle():
    from .train import MODEL_PATH
    if not MODEL_PATH.exists():
        return None
    try:
        import joblib
        return joblib.load(MODEL_PATH)
    except Exception:
        return None


def all_scorers() -> list[Scorer]:
    scorers: list[Scorer] = [AltmanZScore(), MertonScorer()]
    bundle = _load_trained_bundle()
    if bundle is not None:
        scorers.append(TrainedHazardScorer(bundle))
    return scorers
