"""Train the distress-hazard model served by `score.TrainedHazardScorer`.

Input is the panel from `labels.build_real_panel`: one row per firm fiscal year with the
accounting ratios in TRAIN_FEATURES, `label` (1 = default event within a year of the 10-K
becoming public), `date` (fiscal year end) and `firm_id`.

  - Accounting features only. Price history exists mostly for firms that survived, so a model
    fed market features learns "has market data = safe"; market signal stays in Merton as a
    separate cross-check.
  - Monotone constraints (sklearn HistGradientBoosting `monotonic_cst`): leverage and net
    debt/EBITDA can only raise hazard; coverage, liquidity, profitability and size can only
    lower it. EBITDA <= 0 is flagged and pinned above every finite debt/EBITDA, so the
    constraint also holds as EBITDA crosses zero.
  - Expanding-window walk-forward evaluation with a one-horizon embargo: each test year is
    scored by a model trained only on fiscal years ending at least a year before it. Reports AUC
    by year plus, pooled over those years, AUC, top-5%/10% precision, lift and recall and a
    decile calibration table.
  - `prior_correct` turns the case-control probability into a real-world frequency.

`_synthetic_panel` is a plumbing fixture for tests and the self-check; its metrics say nothing
about real-world discrimination, and it is never saved to the serving path.
Real training: python -m app.hazard.labels
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import FunctionTransformer

MODEL_DIR = Path(__file__).resolve().parent / "models"
MODEL_PATH = MODEL_DIR / "trained_hazard.joblib"

# Accounting ratios from features.year_features (training panel and live scoring alike), with
# the monotone sign of each wrt P(distress).
TRAIN_FEATURES = ["leverage", "net_debt_to_ebitda", "ebitda_nonpositive", "interest_coverage",
                  "current_ratio", "quick_ratio", "cash_ratio", "roa", "fcf_margin",
                  "wc_to_assets", "re_to_assets", "size_log_assets", "runway_years"]
MONOTONE = {"leverage": 1, "net_debt_to_ebitda": 1, "ebitda_nonpositive": 1,
            "interest_coverage": -1, "current_ratio": -1, "quick_ratio": -1, "cash_ratio": -1,
            "roa": -1, "fcf_margin": -1, "wc_to_assets": -1, "re_to_assets": -1,
            "size_log_assets": -1, "runway_years": -1}

# Debt/EBITDA is undefined (NaN) at EBITDA <= 0, and a tree may route NaN to the safe side of
# a split. Pinning those rows at a ceiling no real ratio exceeds puts them under the +1
# constraint. Net cash (a negative ratio) reads as zero leverage: otherwise the ratio heads to
# -inf as EBITDA falls toward zero and the constraint would lower PD as the firm weakens.
ND_EBITDA_CEILING = 1e3
# a default with no 10-K for 90 days after FYE drops the row; the horizon then runs a year
LABEL_WINDOW_DAYS = 90 + 365
MIN_TEST_DEFAULTS = 5           # fewer defaults than this in a test year: no AUC reported


def _pin_nonpositive_ebitda(X: np.ndarray, nd: int, flag: int) -> np.ndarray:
    X = np.array(X, dtype=float)
    X[:, nd] = np.where(X[:, flag] == 1, ND_EBITDA_CEILING,
                        np.clip(X[:, nd], 0.0, ND_EBITDA_CEILING))
    return X


def _matrix(df: pd.DataFrame) -> np.ndarray:
    # reindex: absent columns become NaN, which HistGradientBoosting handles natively
    return df.reindex(columns=TRAIN_FEATURES).astype(float).to_numpy()


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


def _fit(X: np.ndarray, y: np.ndarray):
    hgb = HistGradientBoostingClassifier(
        monotonic_cst=[MONOTONE[f] for f in TRAIN_FEATURES],   # native NaN handling, scale-free
        learning_rate=0.05, max_depth=3, max_iter=300, l2_regularization=1.0, random_state=0)
    # column indices travel with the pickled bundle, not module globals
    pin = FunctionTransformer(_pin_nonpositive_ebitda, kw_args={
        "nd": TRAIN_FEATURES.index("net_debt_to_ebitda"),
        "flag": TRAIN_FEATURES.index("ebitda_nonpositive")})
    return make_pipeline(pin, hgb).fit(X, y)


def drop_unobserved(df: pd.DataFrame, observed_through) -> pd.DataFrame:
    """Survivor rows whose label window ends after the last date the event sources cover
    were never observed surviving; training them as 0 teaches the model a false negative."""
    if observed_through is None:
        return df
    window_end = pd.to_datetime(df["date"]) + dt.timedelta(days=LABEL_WINDOW_DAYS)
    return df[~((df["label"] == 0) & (window_end > pd.Timestamp(observed_through)))]


def walk_forward_eval(df: pd.DataFrame) -> dict:
    """Expanding-window walk-forward: per-year AUC, plus — pooled over all out-of-sample
    folds — AUC, precision/lift/recall at top-5%/top-10% flagging thresholds and a predicted-
    probability decile calibration table. Probabilities here are case-control space; the
    King–Zeng prior correction is rank-preserving, so shape and ordering carry over."""
    dates = pd.to_datetime(df["date"])
    yr = dates.dt.year
    aucs: dict[int, float] = {}
    y_parts, p_parts = [], []
    for split in sorted(yr.unique())[1:]:
        # one-horizon embargo: a row's label watches the year after its 10-K, so rows ending
        # within a year of the test year would train on test-period defaults
        cutoff = pd.Timestamp(int(split), 1, 1) - dt.timedelta(days=365)
        tr, te = df[dates < cutoff], df[yr == split]
        if te["label"].sum() < MIN_TEST_DEFAULTS or te["label"].nunique() < 2 or len(tr) < 50:
            continue
        model = _fit(_matrix(tr), tr["label"].to_numpy())
        p = model.predict_proba(_matrix(te))[:, 1]
        aucs[int(split)] = float(roc_auc_score(te["label"], p))
        y_parts.append(te["label"].to_numpy())
        p_parts.append(p)
    out: dict = {"auc_by_year": aucs}
    if not y_parts:
        return out
    y = np.concatenate(y_parts).astype(float)
    p = np.concatenate(p_parts)
    base = float(y.mean())
    order = np.argsort(p)[::-1]
    ops = {}
    for pct in (5, 10):
        k = max(1, int(round(len(p) * pct / 100)))
        flagged = y[order[:k]]
        ops[f"top_{pct}pct"] = {
            "precision": round(float(flagged.mean()), 3),
            "lift": round(float(flagged.mean()) / base, 1) if base > 0 else None,
            "recall": round(float(flagged.sum() / y.sum()), 3) if y.sum() else None,
            "n_flagged": int(k)}
    decile = np.argsort(np.argsort(p)) * 10 // len(p)
    calibration = [{"decile": int(i + 1),
                    "mean_pred": round(float(p[decile == i].mean()), 4),
                    "realized": round(float(y[decile == i].mean()), 4),
                    "n": int((decile == i).sum())}
                   for i in range(10) if (decile == i).any()]
    out.update({"pooled_auc": round(float(roc_auc_score(y, p)), 3),
                "pooled_base_rate": round(base, 4), "operating_points": ops,
                "calibration": calibration})
    return out


def train_from_panel(df: pd.DataFrame, save: bool = True, meta: dict | None = None,
                     observed_through=None):
    """Walk-forward evaluate, then fit a final model on all rows for serving. Returns (aucs, bundle).
    `meta` (e.g. label_source provenance) is merged into the bundle for the UI to display;
    `observed_through` (last date the default sources cover) drops unobserved survivor rows."""
    df = drop_unobserved(df, observed_through)
    evaluation = walk_forward_eval(df)
    aucs = evaluation["auc_by_year"]
    model = _fit(_matrix(df), df["label"].to_numpy())
    bundle = {"model": model, "features": TRAIN_FEATURES, "monotone": MONOTONE,
              "trained_at": dt.datetime.now().isoformat(), "n_rows": int(len(df)),
              "walk_forward_auc": aucs, "eval": evaluation,
              "observed_through": str(observed_through) if observed_through else None,
              **(meta or {})}
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
    aucs, bundle = train_from_panel(df, save=False)   # never write the fixture to the serving path
    print("WARNING: synthetic plumbing fixture — these metrics are NOT a real performance claim.")
    print("walk-forward AUC by test year:")
    for y, a in sorted(aucs.items()):
        print(f"  {y}: {a:.3f}")
    print(f"self-check OK ({bundle['n_rows']} rows) — nothing saved; real training: python -m app.hazard.labels")
