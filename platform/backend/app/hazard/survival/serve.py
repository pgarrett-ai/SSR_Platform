"""Lazy, request-cheap serving seam for the 5-model survival panel.

Mirrors the `TrainedHazardScorer` / `_load_trained_bundle` pattern in hazard/score.py:
a bundle produced offline by fit.py, loaded once and cached, degrading to `None` (section
absent from the payload) when no bundle has been fit yet.

Heavy-dep honesty: features.py/evaluate.py/models.py import sksurv/lifelines at module
top, so this module's own top-level imports stay stdlib-only; `joblib.load` and
`build_model_matrix` are imported lazily inside the functions, after the bundle-exists
check — sksurv/lifelines never load without a bundle on disk.
"""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def _load_bundle():
    from .fit import SURVIVAL_PATH
    if not SURVIVAL_PATH.exists():
        return None
    try:
        # joblib.load = pickle; safe here — the bundle is a locally-produced artifact
        # written only by our own fit.py (same trust model as trained_hazard.joblib in
        # score.py), never downloaded. Unpickling the fitted models pulls models.py (and
        # therefore sksurv/lifelines) in HERE, only once a bundle actually exists.
        import joblib
        return joblib.load(SURVIVAL_PATH)
    except Exception:
        return None


def survival_panel(feats: dict, market=None) -> dict | None:
    """5-model PD@1y + holdout concordance, keyed by model name. `None` when no bundle
    has been fit yet — the frontend panel simply doesn't render."""
    b = _load_bundle()
    if b is None:
        return None                                            # degradation: bundle absent
    import numpy as np
    import pandas as pd
    from .features import build_model_matrix                   # lazy — bundle exists, safe

    # equity_vol/excess_return always present (as None if unfilled) so build_model_matrix
    # never KeyErrors on a bundle that was trained with market coverage but is being served
    # without a live market snapshot — serve-time imputation (below) covers the gap instead.
    row = {
        "retained_earnings_to_assets": feats.get("re_to_assets"),
        **{k: feats.get(k) for k in ("size_log_assets", "leverage", "roa", "interest_coverage",
                                     "current_ratio", "cash_ratio", "wc_to_assets")},
        "equity_vol": None, "excess_return": None,
    }
    if market is not None and getattr(market, "ok", False):
        row["equity_vol"] = market.equity_vol
        row["excess_return"] = market.excess_return_1y

    df = pd.DataFrame([row])
    X, _ = build_model_matrix(df, b["features"])
    med = np.asarray(b["medians"], dtype=float)
    X = np.where(np.isnan(X), med, X)                          # serve-time impute (missing live feats)
    Xs = b["scaler"].transform(X)

    out = []
    for r in b["results"]:                                     # already sorted by c_index desc
        s = float(b["models"][r["model"]].survival(Xs, [1.0])[0][0])
        out.append({"name": r["model"], "pd_1y": round(1.0 - s, 4),
                    "c_index": round(r["c_index"], 3), "ibs": round(r["ibs"], 3)})
    return {"available": True, "models": out, "label_source": b.get("label_source"),
            "note": f"5-model survival panel · holdout concordance · {b['n_firms']} firms"}
