"""P6 framework: train a distress-hazard model that slots into the Scorer interface.

The MVP scorers (Altman/Merton/CHS) need no training. This adds the *framework* so a real trained
model can drop in behind the same `Scorer` contract, honoring the two audit requirements the
scorecards can't:
  - **Monotonic constraints (audit H2):** leverage/net-debt can only *raise* predicted hazard;
    coverage/liquidity/profitability/size can only *lower* it. Enforced via sklearn's
    HistGradientBoostingClassifier `monotonic_cst` (no LightGBM dependency needed).
  - **Walk-forward validation (audit C3):** expanding window — train on prior years, test on the
    next. Never evaluate on the past. Cross-sectional CV would inflate metrics (defaults cluster
    in time).

THE MISSING INPUT IS REAL LABELS. `train_from_panel` expects a DataFrame with TRAIN_FEATURES +
`label` (1 = distress event within the forward horizon) + `date` (period end) + `firm_id`. The
real label source is EDGAR 8-K Item 1.03 filings / the UCLA-LoPucki BRD (not wired yet).

`_synthetic_panel` exists ONLY as a plumbing fixture for the self-check — it is NOT real data and
its metrics are NOT a performance claim. (The audit flagged exactly this: numbers on synthetic
data say nothing about real-world discrimination.) No synthetic model is ever shipped to the
dashboard — `score.all_scorers()` only picks up a model deliberately saved to models/.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

MODEL_DIR = Path(__file__).resolve().parent / "models"
MODEL_PATH = MODEL_DIR / "trained_hazard.joblib"

# Features (keys produced by features.year_features) + monotone sign wrt P(distress).
TRAIN_FEATURES = ["leverage", "net_debt_to_ebitda", "interest_coverage", "current_ratio",
                  "quick_ratio", "cash_ratio", "roa", "fcf_margin", "wc_to_assets",
                  "re_to_assets", "size_log_assets"]
MONOTONE = {"leverage": 1, "net_debt_to_ebitda": 1, "interest_coverage": -1, "current_ratio": -1,
            "quick_ratio": -1, "cash_ratio": -1, "roa": -1, "fcf_margin": -1, "wc_to_assets": -1,
            "re_to_assets": -1, "size_log_assets": -1}


def _matrix(df: pd.DataFrame) -> np.ndarray:
    return df[TRAIN_FEATURES].astype(float).to_numpy()


def prior_correct(p: float, sample_rate: float, true_rate: float) -> float:
    """King & Zeng (2001) prior correction for case-control sampling: shift the logit by
    the true-vs-sample base-rate log-odds offset. Rank-preserving (AUC unchanged); turns
    the classifier's case-control probability into a real-world frequency."""
    import math
    p = min(max(p, 1e-6), 1 - 1e-6)
    off = (math.log(true_rate / (1 - true_rate)) -
           math.log(sample_rate / (1 - sample_rate)))
    logit = math.log(p / (1 - p)) + off
    return 1.0 / (1.0 + math.exp(-logit))


def _fit(X: np.ndarray, y: np.ndarray) -> HistGradientBoostingClassifier:
    m = HistGradientBoostingClassifier(
        monotonic_cst=[MONOTONE[f] for f in TRAIN_FEATURES],   # native NaN handling, scale-free
        learning_rate=0.05, max_depth=3, max_iter=300, l2_regularization=1.0, random_state=0)
    m.fit(X, y)
    return m


def walk_forward_auc(df: pd.DataFrame) -> dict[int, float]:
    """Expanding-window AUC: for each year, train on all prior years, test on that year."""
    yr = pd.to_datetime(df["date"]).dt.year
    out: dict[int, float] = {}
    for split in sorted(yr.unique())[1:]:
        tr, te = df[yr < split], df[yr == split]
        if te["label"].nunique() < 2 or len(tr) < 50:
            continue
        model = _fit(_matrix(tr), tr["label"].to_numpy())
        out[int(split)] = float(roc_auc_score(te["label"], model.predict_proba(_matrix(te))[:, 1]))
    return out


def train_from_panel(df: pd.DataFrame, save: bool = True, meta: dict | None = None):
    """Walk-forward evaluate, then fit a final model on all rows for serving. Returns (aucs, bundle).
    `meta` (e.g. label_source provenance) is merged into the bundle for the UI to display."""
    aucs = walk_forward_auc(df)
    model = _fit(_matrix(df), df["label"].to_numpy())
    bundle = {"model": model, "features": TRAIN_FEATURES, "monotone": MONOTONE,
              "trained_at": dt.datetime.now().isoformat(), "n_rows": int(len(df)),
              "walk_forward_auc": aucs, **(meta or {})}
    if save:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        import joblib
        joblib.dump(bundle, MODEL_PATH)
    return aucs, bundle


def _synthetic_panel(n_firms: int = 400, years: int = 8, seed: int = 0) -> pd.DataFrame:
    """PLUMBING FIXTURE ONLY — not real data, not a performance claim (see module docstring)."""
    rng = np.random.default_rng(seed)
    rows = []
    for fid in range(n_firms):
        q = rng.standard_normal()  # latent credit quality (higher = healthier)
        for yr in range(2016, 2016 + years):
            pd_true = 1.0 / (1.0 + np.exp(-(-2.5 - 1.5 * q)))
            rows.append({
                "firm_id": fid, "date": f"{yr}-12-31",
                "leverage": float(np.clip(0.45 - 0.10 * q + 0.15 * rng.standard_normal(), 0.01, 1.5)),
                "net_debt_to_ebitda": float(3 - q + rng.standard_normal()),
                "interest_coverage": float(np.clip(np.exp(1.3 + 0.5 * q + 0.5 * rng.standard_normal()), 0.05, 50)),
                "current_ratio": float(np.clip(1.5 + 0.2 * q + 0.3 * rng.standard_normal(), 0.1, 5)),
                "quick_ratio": float(np.clip(1.0 + 0.2 * q + 0.3 * rng.standard_normal(), 0.05, 4)),
                "cash_ratio": float(np.clip(0.20 + 0.05 * q + 0.05 * rng.standard_normal(), 0, 1)),
                "roa": float(0.04 + 0.05 * q + 0.04 * rng.standard_normal()),
                "fcf_margin": float(0.05 + 0.05 * q + 0.05 * rng.standard_normal()),
                "wc_to_assets": float(0.12 + 0.10 * q + 0.10 * rng.standard_normal()),
                "re_to_assets": float(0.10 + 0.20 * q + 0.15 * rng.standard_normal()),
                "size_log_assets": float(7 + 0.6 * q + 0.5 * rng.standard_normal()),
                "label": int(rng.uniform() < pd_true * 0.3),
            })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = _synthetic_panel()
    aucs, bundle = train_from_panel(df, save=True)
    print("WARNING: synthetic plumbing fixture — these metrics are NOT a real performance claim.")
    print("walk-forward AUC by test year:")
    for y, a in sorted(aucs.items()):
        print(f"  {y}: {a:.3f}")
    print(f"saved bundle ({bundle['n_rows']} rows) -> {MODEL_PATH}")
