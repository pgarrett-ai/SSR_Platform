"""Evaluation: the metrics that matter for a credit survival model.

- **Concordance index (C-index)** - discrimination: does the model rank a firm
  that defaults sooner as riskier? 0.5 = coin flip, 1.0 = perfect ranking.
- **Integrated Brier Score (IBS)** - calibration: are the predicted survival
  *probabilities* accurate over time? Lower is better; ~0.25 is uninformative.
  (This is the metric that is easy to quote backwards - low is good, not high.)
- **Time-dependent AUC** - discrimination evaluated at specific horizons.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sksurv.metrics import (
    concordance_index_censored,
    integrated_brier_score,
    cumulative_dynamic_auc,
)


def time_grid(y_train, y_test, n: int = 12) -> np.ndarray:
    """Evaluation times inside the follow-up window of both train and test.

    Bounded by event-time percentiles and kept strictly inside the maximum
    observed times so the censoring estimator stays defined.
    """
    ev = y_test["time"][y_test["event"]]
    lo = max(np.percentile(ev, 10), y_train["time"].min() + 1e-3, 1e-2)
    hi = min(
        np.percentile(ev, 90),
        y_train["time"].max() * 0.999,
        y_test["time"].max() * 0.999,
    )
    return np.linspace(lo, hi, n)


def evaluate_model(model, X_test, y_train, y_test, times) -> dict:
    """Compute C-index, IBS, and mean time-dependent AUC for a fitted model."""
    risk = model.risk(X_test)
    cindex = concordance_index_censored(
        y_test["event"].astype(bool), y_test["time"], risk
    )[0]

    surv = model.survival(X_test, times)
    surv = np.clip(np.asarray(surv, dtype=float), 1e-7, 1 - 1e-7)
    ibs = integrated_brier_score(y_train, y_test, surv, times)

    _, mean_auc = cumulative_dynamic_auc(y_train, y_test, risk, times)

    return {
        "model": model.name,
        "c_index": float(cindex),
        "ibs": float(ibs),
        "mean_auc": float(mean_auc),
    }


def compare_models(models, X_train, y_train, X_test, y_test, times, feature_names) -> tuple[pd.DataFrame, dict]:
    """Fit and evaluate every model. Returns (results table, fitted models dict)."""
    rows = []
    fitted = {}
    for m in models:
        m.fit(X_train, y_train, feature_names)
        rows.append(evaluate_model(m, X_test, y_train, y_test, times))
        fitted[m.name] = m
    df = (
        pd.DataFrame(rows)
        .sort_values("c_index", ascending=False)
        .reset_index(drop=True)
    )
    return df, fitted
