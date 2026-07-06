"""Score a single firm: turn fitted-model output into a PD term structure.

This is the bridge to the rest of the distressed-credit toolkit - a probability
of default by horizon that can feed an expected-loss calc (pair the PD with a
recovery estimate from the Fulcrum waterfall engine) or a watchlist screen.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .features import build_model_matrix


def score_firm(model, scaler, firm, horizons=(1.0, 3.0, 5.0)) -> dict:
    """Predict survival and cumulative default probability for one firm.

    Parameters
    ----------
    model : a fitted SurvivalModel.
    scaler : the fitted StandardScaler used in training.
    firm : dict or single-row DataFrame of raw FEATURES values.
    horizons : years at which to report cumulative PD.

    Returns
    -------
    dict with `survival` and `pd` (cumulative default prob) keyed by horizon.
    """
    df = pd.DataFrame([firm]) if isinstance(firm, dict) else firm.reset_index(drop=True)
    X, _ = build_model_matrix(df)
    Xs = scaler.transform(X)
    horizons = np.asarray(horizons, dtype=float)
    surv = model.survival(Xs, horizons)[0]
    return {
        "survival": {f"{h:g}y": float(s) for h, s in zip(horizons, surv)},
        "pd": {f"{h:g}y": float(1.0 - s) for h, s in zip(horizons, surv)},
    }
