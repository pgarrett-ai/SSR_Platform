"""Survival models behind one interface.

Every model implements:

    fit(X, y, feature_names)      X: (n, p) scaled array, y: sksurv structured array
    risk(X)      -> (n,)          higher = more likely to default sooner  (for C-index)
    survival(X, times) -> (n, T)  survival probability at each time in `times` (for Brier)

The lineup spans the methodological range you'd actually compare in credit:
a linear Cox model and a parametric Weibull AFT (interpretable), a Random Survival
Forest and gradient-boosted survival ensemble (nonparametric ML), and a
from-scratch discrete-time (Shumway-style) hazard - the workhorse of academic
bankruptcy prediction.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from lifelines import CoxPHFitter, WeibullAFTFitter
from sksurv.ensemble import RandomSurvivalForest, GradientBoostingSurvivalAnalysis


def _df(X: np.ndarray, names: list[str]) -> pd.DataFrame:
    return pd.DataFrame(X, columns=names)


class SurvivalModel:
    name = "base"

    def fit(self, X, y, feature_names):
        raise NotImplementedError

    def risk(self, X):
        raise NotImplementedError

    def survival(self, X, times):
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Lifelines: Cox proportional hazards
# --------------------------------------------------------------------------- #
class CoxModel(SurvivalModel):
    name = "Cox PH"

    def __init__(self, penalizer: float = 0.01):
        self.penalizer = penalizer

    def fit(self, X, y, feature_names):
        self.names_ = list(feature_names)
        df = _df(X, self.names_)
        df["duration"] = y["time"]
        df["event"] = y["event"].astype(int)
        self.model_ = CoxPHFitter(penalizer=self.penalizer)
        self.model_.fit(df, duration_col="duration", event_col="event")
        return self

    def risk(self, X):
        return self.model_.predict_partial_hazard(_df(X, self.names_)).to_numpy().ravel()

    def survival(self, X, times):
        sf = self.model_.predict_survival_function(_df(X, self.names_), times=times)
        return sf.to_numpy().T  # (n_samples, n_times)

    def coefficients(self) -> pd.Series:
        return self.model_.params_


# --------------------------------------------------------------------------- #
# Lifelines: Weibull accelerated failure time
# --------------------------------------------------------------------------- #
class WeibullAFTModel(SurvivalModel):
    name = "Weibull AFT"

    def fit(self, X, y, feature_names):
        self.names_ = list(feature_names)
        df = _df(X, self.names_)
        df["duration"] = y["time"]
        df["event"] = y["event"].astype(int)
        self.t_ref_ = float(np.median(y["time"]))
        self.model_ = WeibullAFTFitter(penalizer=0.01)
        self.model_.fit(df, duration_col="duration", event_col="event")
        return self

    def survival(self, X, times):
        sf = self.model_.predict_survival_function(_df(X, self.names_), times=times)
        return sf.to_numpy().T

    def risk(self, X):
        # 1 - S(t_ref): higher = riskier. Monotone, well-defined for AFT.
        s_ref = self.survival(X, [self.t_ref_])[:, 0]
        return 1.0 - s_ref


# --------------------------------------------------------------------------- #
# scikit-survival ensembles
# --------------------------------------------------------------------------- #
class _SksurvModel(SurvivalModel):
    estimator_cls = None
    kwargs: dict = {}

    def fit(self, X, y, feature_names):
        self.names_ = list(feature_names)
        self.model_ = self.estimator_cls(**self.kwargs)
        self.model_.fit(np.asarray(X), y)
        return self

    def risk(self, X):
        return self.model_.predict(np.asarray(X))

    def survival(self, X, times):
        # Vectorized step-function evaluation: get survival at the model's event
        # times once, then index to the requested times (much faster than calling
        # one StepFunction object per sample).
        surv = self.model_.predict_survival_function(np.asarray(X), return_array=True)
        # RSF exposes unique_times_, GBSA exposes event_times_ - support both.
        et = getattr(self.model_, "unique_times_", None)
        if et is None:
            et = self.model_.event_times_
        et = np.asarray(et, dtype=float)
        times = np.asarray(times, dtype=float)
        idx = np.searchsorted(et, times, side="right") - 1
        cols = np.clip(idx, 0, len(et) - 1)
        out = surv[:, cols]
        out[:, idx < 0] = 1.0  # before the first event time, survival = 1
        return out


class RandomForestModel(_SksurvModel):
    name = "Random Survival Forest"
    estimator_cls = RandomSurvivalForest
    kwargs = dict(n_estimators=150, min_samples_leaf=25, max_features="sqrt",
                  n_jobs=-1, random_state=0)


class GradientBoostedModel(_SksurvModel):
    name = "Gradient-Boosted Survival"
    estimator_cls = GradientBoostingSurvivalAnalysis
    kwargs = dict(n_estimators=200, learning_rate=0.05, max_depth=3,
                  subsample=0.7, random_state=0)


# --------------------------------------------------------------------------- #
# From-scratch discrete-time (Shumway-style) hazard
# --------------------------------------------------------------------------- #
class DiscreteTimeHazardModel(SurvivalModel):
    """Discrete-time hazard via logistic regression on a person-period panel.

    Each firm contributes one row per year it is observed; the label is 1 only in
    the year it defaults. A log-time term gives a flexible baseline hazard. This
    is the Shumway (2001) hazard model, the standard parametric approach to
    bankruptcy prediction.
    """

    name = "Discrete-Time Hazard"

    def __init__(self, horizon_ref: float | None = None):
        self.horizon_ref = horizon_ref

    def fit(self, X, y, feature_names):
        self.names_ = list(feature_names)
        X = np.asarray(X)
        dur = np.asarray(y["time"], dtype=float)
        evt = np.asarray(y["event"]).astype(int)

        m = np.maximum(np.ceil(dur).astype(int), 1)  # periods observed per firm
        rows_X = np.repeat(X, m, axis=0)
        period = np.concatenate([np.arange(1, mi + 1) for mi in m]).astype(float)
        # label: 1 in the final period iff the firm defaulted
        labels = np.zeros(m.sum(), dtype=int)
        ends = np.cumsum(m) - 1
        labels[ends] = evt

        design = np.column_stack([rows_X, np.log(period)])
        self.clf_ = LogisticRegression(max_iter=1000, C=2.0)
        self.clf_.fit(design, labels)
        self.max_period_ = int(m.max())
        self.t_ref_ = float(self.horizon_ref or np.median(dur))
        return self

    def _hazard(self, X, t):
        design = np.column_stack([X, np.full(X.shape[0], np.log(t))])
        return self.clf_.predict_proba(design)[:, 1]

    def _survival_integer(self, X):
        # S at integer years 1..max_period: cumulative product of (1 - hazard).
        X = np.asarray(X)
        haz = np.column_stack([self._hazard(X, t) for t in range(1, self.max_period_ + 1)])
        return np.cumprod(1.0 - haz, axis=1)  # (n, max_period)

    def survival(self, X, times):
        surv_int = self._survival_integer(X)  # index j -> survival through year j+1
        times = np.asarray(times, dtype=float)
        out = np.ones((surv_int.shape[0], len(times)))
        for j, t in enumerate(times):
            idx = int(np.floor(t)) - 1  # survival through the last completed year
            if idx < 0:
                out[:, j] = 1.0
            else:
                idx = min(idx, surv_int.shape[1] - 1)
                out[:, j] = surv_int[:, idx]
        return out

    def risk(self, X):
        return 1.0 - self.survival(X, [self.t_ref_])[:, 0]


def all_models() -> list[SurvivalModel]:
    """Fresh instances of every model, in presentation order."""
    return [
        CoxModel(),
        WeibullAFTModel(),
        DiscreteTimeHazardModel(),
        RandomForestModel(),
        GradientBoostedModel(),
    ]
