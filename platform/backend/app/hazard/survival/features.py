"""Feature definitions, transforms, and survival-data plumbing.

The feature set is the classic credit-risk vocabulary - the Altman Z ingredients
(working capital, retained earnings, EBIT, leverage) plus the Shumway (2001)
market variables (size, equity volatility, excess return). A few ratios are
heavily right-skewed (coverage, current ratio, volatility); those get a log
transform before they enter any model.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sksurv.util import Surv

# Order matters: TRUE_BETA in data.py is aligned to this list.
FEATURES: list[str] = [
    "size_log_assets",            # log total assets ($mm)         (-) bigger is safer
    "leverage",                   # total debt / assets            (+)
    "roa",                        # EBIT / assets                  (-)
    "interest_coverage",          # EBIT / interest expense        (-)  [log]
    "current_ratio",              # current assets / current liab  (-)  [log]
    "cash_ratio",                 # cash / assets                  (-)
    "retained_earnings_to_assets",# RE / assets (Altman)           (-)
    "wc_to_assets",               # working capital / assets       (-)
    "sales_growth",               # YoY sales growth               (-)
    "equity_vol",                 # annualized equity volatility   (+)  [log]
    "excess_return",              # excess equity return vs market (-)
]

# Skewed features that enter models in log space.
LOG_FEATURES: set[str] = {"interest_coverage", "current_ratio", "equity_vol"}

_TINY = 1e-6


def build_model_matrix(df: pd.DataFrame, features: list[str] = FEATURES) -> tuple[np.ndarray, list[str]]:
    """Return the numeric model matrix (log-transformed where appropriate).

    Column order follows `features` (defaults to the full FEATURES list, so every
    existing caller is unaffected). Names of log-transformed columns are prefixed
    with 'log_' so coefficient tables read correctly.
    """
    cols = []
    names = []
    for f in features:
        x = df[f].to_numpy(dtype=float)
        if f in LOG_FEATURES:
            x = np.log(np.clip(x, _TINY, None))
            names.append(f"log_{f}")
        else:
            names.append(f)
        cols.append(x)
    return np.column_stack(cols), names


def make_surv(event: np.ndarray, duration: np.ndarray):
    """Build a scikit-survival structured array (event bool, time float)."""
    return Surv.from_arrays(event=event.astype(bool), time=duration.astype(float))


def split_by_firm(df: pd.DataFrame, test_size: float = 0.3, seed: int = 0):
    """Train/test split at the firm level (no firm appears in both)."""
    rng = np.random.default_rng(seed)
    firms = df["firm_id"].unique()
    rng.shuffle(firms)
    n_test = int(round(test_size * len(firms)))
    test_firms = set(firms[:n_test].tolist())
    is_test = df["firm_id"].isin(test_firms).to_numpy()
    return df.loc[~is_test].copy(), df.loc[is_test].copy()
