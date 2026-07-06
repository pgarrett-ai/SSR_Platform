"""Distress scorers behind one interface.

    score(feats, market)         -> dict   headline value + zone/PD + availability
    contributions(feats, market) -> dict   per-feature additive decomposition (the "SHAP" panel)

The MVP scorers use *published coefficients* (no training, nothing to overfit), so the
contribution panel is exact: for a linear/logit model the contribution of feature i is
beta_i * x_i and the parts sum to the score (Altman) or the logit (CHS). A future
`TrainedHazardScorer(Scorer)` (Phase 6) loads a fitted LightGBM and returns SHAP values from
the same two methods — the dashboard consumes the interface, not the implementation.

References:
  Altman, E. (2005) Z''-score for non-manufacturers / emerging markets.
  Merton, R. (1974); see merton.py.
  Campbell, Hilscher, Szilagyi (2008) "In Search of Distress Risk", J. Finance — Table IV.
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Optional

from . import merton as merton_mod
from ..core.config import get_settings
from .features import book_equity, total_debt


class Scorer:
    name: str = "base"
    higher_is_safer: bool = True   # how the frontend colors the contribution bar

    def score(self, feats: dict, market=None) -> dict:
        raise NotImplementedError

    def contributions(self, feats: dict, market=None) -> Optional[dict]:
        return None


# --------------------------------------------------------------------------- #
# Altman Z'' (accounting-only -> works for every fiscal year, no market data)
# --------------------------------------------------------------------------- #
class AltmanZScore(Scorer):
    name = "Altman Z''"
    higher_is_safer = True
    INTERCEPT = 3.25
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
        z = self.INTERCEPT + sum(terms.values())
        zone = "distress" if z < 1.1 else ("grey" if z < 2.6 else "safe")
        return {"available": True, "value": round(z, 2), "zone": zone}

    def contributions(self, feats: dict, market=None) -> Optional[dict]:
        terms = self._terms(feats)
        if terms is None:
            return None
        terms["(baseline)"] = self.INTERCEPT
        return terms


# --------------------------------------------------------------------------- #
# Merton DD -> PD term structure (needs market equity value + vol)
# --------------------------------------------------------------------------- #
class MertonScorer(Scorer):
    name = "Merton DD"
    higher_is_safer = True

    @staticmethod
    def default_point(feats: dict, yf=None) -> Optional[float]:
        # KMV default point ~ short-term debt + half long-term debt; fall back to total debt.
        td = feats.get("total_debt")
        return td

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
        }


# --------------------------------------------------------------------------- #
# Campbell-Hilscher-Szilagyi hazard (EXPERIMENTAL point-in-time approximation)
# --------------------------------------------------------------------------- #
class CHSHazard(Scorer):
    """CHS (2008) 12-month failure logit with published coefficients.

    Labeled experimental: CHS uses geometrically-weighted averages (NIMTAAVG, EXRETAVG); here
    we substitute point-in-time NIMTA and 1y excess return, so the level is an approximation —
    read the ranking/contributions, not the absolute PD. Exact averaging is a Phase-2 item.
    ponytail: point-in-time inputs; upgrade to the geometric averages once quarterly data lands.
    """
    name = "CHS hazard (exp.)"
    higher_is_safer = False
    INTERCEPT = -9.164
    COEF = {
        "NIMTA": -20.264, "TLMTA": 1.416, "EXRET": -7.129, "SIGMA": 1.411,
        "RSIZE": -0.045, "CASHMTA": -2.132, "MB": 0.075, "PRICE": -0.058,
    }
    _US_EQUITY_MV = 40e12   # crude total-market proxy for RSIZE (tiny coefficient)

    def _vars(self, feats: dict, market) -> Optional[dict]:
        if market is None or not market.ok or market.market_cap is None:
            return None
        me = market.market_cap
        tl = feats.get("total_liabilities")
        ni = feats.get("net_income")
        cash = feats.get("cash")
        be = feats.get("book_equity")
        if None in (tl, ni, cash, be) or market.equity_vol is None or market.price is None:
            return None
        mta = me + tl
        if mta <= 0:
            return None
        # CHS floor book equity so market-to-book stays finite for negative-equity (distressed)
        # firms — exactly the names that matter. The paper adjusts BE; a small positive floor
        # is the minimal robust version. ponytail: floor only; full BE adjustment is Phase 2.
        be_eff = be if be > 0 else max(0.05 * me, 1.0)
        return {
            "NIMTA": ni / mta,
            "TLMTA": tl / mta,
            "EXRET": market.excess_return_1y or 0.0,
            "SIGMA": market.equity_vol,
            "RSIZE": math.log(me / self._US_EQUITY_MV),
            "CASHMTA": cash / mta,
            "MB": me / be_eff,
            "PRICE": math.log(min(market.price, 15.0)),
        }

    def score(self, feats: dict, market=None) -> dict:
        v = self._vars(feats, market)
        if v is None:
            return {"available": False, "note": "needs market + net income/liabilities"}
        logit = self.INTERCEPT + sum(self.COEF[k] * v[k] for k in self.COEF)
        pd12 = 1.0 / (1.0 + math.exp(-logit))
        return {"available": True, "value": round(pd12, 4), "logit": round(logit, 2),
                "experimental": True}

    def contributions(self, feats: dict, market=None) -> Optional[dict]:
        v = self._vars(feats, market)
        if v is None:
            return None
        terms = {k: self.COEF[k] * v[k] for k in self.COEF}
        terms["(baseline)"] = self.INTERCEPT
        return terms


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
    """A fitted hazard model behind the Scorer interface (see train.py).

    Tree model → no exact linear decomposition, so `contributions` stays None; the Altman/CHS
    panels carry attribution. Swap in SHAP here if a contribution panel is wanted later.
    """
    name = "Trained hazard"
    higher_is_safer = False

    def __init__(self, bundle: dict):
        self.model = bundle["model"]
        self.features = bundle["features"]
        self.label_source = bundle.get("label_source")   # real-label provenance (Phase 5)
        self.walk_forward_auc = bundle.get("walk_forward_auc") or {}
        self.prior = bundle.get("prior")                 # case-control -> real-world rates

    def score(self, feats: dict, market=None) -> dict:
        import numpy as np
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
    scorers: list[Scorer] = [AltmanZScore(), MertonScorer(), CHSHazard()]
    bundle = _load_trained_bundle()
    if bundle is not None:
        scorers.append(TrainedHazardScorer(bundle))
    return scorers
