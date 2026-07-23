"""Offline fit: build the 5-model survival bundle from the real credit panel
(labels.build_real_panel — the same panel.db-backed source the HistGBM trainer uses).

    python -m app.hazard.survival.fit [--defaulters N] [--controls N]

Heavy-dep laziness: SURVIVAL_PATH must stay importable (by serve.py's _load_bundle)
without pulling in sksurv/lifelines, so the survival-package imports (features/evaluate/
models, all heavy at their own module top) are deferred into the functions that use them
rather than sitting at this module's top.
"""

from __future__ import annotations

import datetime as dt

import joblib
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from ..train import MODEL_DIR

SURVIVAL_PATH = MODEL_DIR / "survival_panel.joblib"   # gitignored: root .gitignore *.joblib

# Real-panel (year_features + pit_market_features) column -> survival FEATURES name.
RENAME = {"re_to_assets": "retained_earnings_to_assets", "excess_return_1y": "excess_return"}


def panel_to_survival(df: pd.DataFrame, features: list[str]):
    """Person-period (firm_id, date, label, +feats) -> firm-level baseline-covariate survival.

    Covariates are taken as-of the entry (earliest) year per firm — no look-ahead. Duration
    is years between the first and last kept row; event is 1 if the firm ever defaulted.
    ponytail: fiscal-year-granular duration; labels.py drops post-petition rows so the last
    kept year ~= last pre-default year. Exact 8-K dates would sharpen -> upgrade path.
    """
    from .features import build_model_matrix, make_surv

    df = df.rename(columns=RENAME).copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["firm_id", "date"])
    g = df.groupby("firm_id", sort=False)
    entry = g.first()                          # covariates AS OF entry year
    dur = ((g["date"].max() - g["date"].min()).dt.days / 365.25 + 1.0).clip(lower=1.0)
    event = g["label"].max().astype(int)       # ever defaulted within horizon
    X, names = build_model_matrix(entry.reset_index(), features)   # log-transforms applied
    return X, names, make_surv(event.to_numpy(), dur.to_numpy())


def fit_survival_bundle(df: pd.DataFrame, label_source: str | None = None, save: bool = True) -> dict:
    from .features import FEATURES, split_by_firm
    from .evaluate import compare_models, time_grid
    from .models import all_models

    fit_features = [f for f in FEATURES if f != "sales_growth"]
    renamed = df.rename(columns=RENAME)
    feats = [f for f in fit_features
             if renamed.reindex(columns=[f]).notna().mean().item() >= 0.5]

    tr, te = split_by_firm(df, 0.3, seed=0)
    Xtr, names, ytr = panel_to_survival(tr, feats)
    Xte, _, yte = panel_to_survival(te, feats)
    med = np.nanmedian(Xtr, axis=0)            # sksurv/lifelines crash on NaN -> impute
    Xtr = np.where(np.isnan(Xtr), med, Xtr)
    Xte = np.where(np.isnan(Xte), med, Xte)
    scaler = StandardScaler().fit(Xtr)
    times = time_grid(ytr, yte, n=12)
    results, _ = compare_models(all_models(), scaler.transform(Xtr), ytr,
                                scaler.transform(Xte), yte, times, names)  # holdout metrics

    Xall, _, yall = panel_to_survival(df, feats)
    Xall = np.where(np.isnan(Xall), med, Xall)
    Xs = scaler.fit_transform(Xall)
    models = {m.name: m.fit(Xs, yall, names) for m in all_models()}   # final fit on ALL firms

    bundle = {
        "models": models, "scaler": scaler, "features": feats, "medians": med.tolist(),
        "results": results.to_dict("records"), "label_source": label_source,
        "n_firms": int(df["firm_id"].nunique()), "trained_at": dt.datetime.now().isoformat(),
    }
    if save:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump(bundle, SURVIVAL_PATH)
    return bundle


if __name__ == "__main__":
    import argparse

    from ..labels import build_real_panel, load_or_harvest_events

    ap = argparse.ArgumentParser()
    ap.add_argument("--defaulters", type=int, default=120)
    ap.add_argument("--controls", type=int, default=120)
    args = ap.parse_args()

    print("1/2 loading 8-K Item 1.03 events + building real panel from XBRL…")
    events = load_or_harvest_events()
    df = build_real_panel(events, args.defaulters, args.controls)

    print("2/2 fitting 5-model survival panel…")
    label_source = (f"8-K Item 1.03 harvest ({int(df['label'].sum())} default firm-years / "
                    f"{len(df)} rows, {df['firm_id'].nunique()} firms)")
    bundle = fit_survival_bundle(df, label_source=label_source, save=True)
    print(f"saved {SURVIVAL_PATH} — {bundle['n_firms']} firms, features: {bundle['features']}")
    for r in bundle["results"]:
        print(f"  {r['model']}: c_index={r['c_index']:.3f} ibs={r['ibs']:.3f}")
