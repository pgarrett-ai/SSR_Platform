"""Smoke + sanity tests for the hazard pipeline.

Run with `pytest` or `python -m tests.test_pipeline`.
"""

from __future__ import annotations

import numpy as np
from sklearn.preprocessing import StandardScaler

from app.hazard.survival import (
    PanelConfig,
    TRUE_BETA,
    FEATURES,
    generate_panel,
    build_model_matrix,
    split_by_firm,
    make_surv,
    all_models,
    compare_models,
    time_grid,
    score_firm,
)
# Example firm profiles (inlined from hazard/examples/train_and_evaluate.py at the merge).
HEALTHY = {
    "size_log_assets": 8.5, "leverage": 0.20, "roa": 0.12, "interest_coverage": 12.0,
    "current_ratio": 2.2, "cash_ratio": 0.18, "retained_earnings_to_assets": 0.40,
    "wc_to_assets": 0.30, "sales_growth": 0.12, "equity_vol": 0.25, "excess_return": 0.15,
}
DISTRESSED = {
    "size_log_assets": 6.2, "leverage": 0.80, "roa": -0.02, "interest_coverage": 1.6,
    "current_ratio": 0.95, "cash_ratio": 0.04, "retained_earnings_to_assets": -0.05,
    "wc_to_assets": 0.00, "sales_growth": -0.08, "equity_vol": 0.65, "excess_return": -0.15,
}


def _fit_small():
    df = generate_panel(PanelConfig(n_firms=1500, seed=3))
    train_df, test_df = split_by_firm(df, test_size=0.3, seed=2)
    Xtr_raw, names = build_model_matrix(train_df)
    Xte_raw, _ = build_model_matrix(test_df)
    scaler = StandardScaler().fit(Xtr_raw)
    Xtr, Xte = scaler.transform(Xtr_raw), scaler.transform(Xte_raw)
    ytr = make_surv(train_df["event"].to_numpy(), train_df["duration"].to_numpy())
    yte = make_surv(test_df["event"].to_numpy(), test_df["duration"].to_numpy())
    times = time_grid(ytr, yte, n=8)
    results, fitted = compare_models(all_models(), Xtr, ytr, Xte, yte, times, names)
    return df, results, fitted, scaler


def test_panel_is_well_formed():
    df = generate_panel(PanelConfig(n_firms=2000, seed=5))
    assert not df.isna().any().any()
    rate = df["event"].mean()
    assert 0.05 < rate < 0.6, f"implausible default rate {rate:.2f}"
    assert (df["duration"] > 0).all()
    assert (df["duration"] <= PanelConfig().horizon + 1e-9).all()


def test_models_discriminate():
    # Every model should beat a coin flip; the best should be clearly skillful.
    _, results, _, _ = _fit_small()
    assert (results["c_index"] > 0.55).all()
    assert results["c_index"].max() > 0.72
    assert (results["ibs"] < 0.25).all()  # better than uninformative


def test_cox_recovers_signs():
    _, _, fitted, _ = _fit_small()
    coefs = fitted["Cox PH"].coefficients()
    names = list(coefs.index)
    ok = sum(np.sign(coefs[n]) == np.sign(TRUE_BETA[f]) for n, f in zip(names, FEATURES))
    assert ok >= len(FEATURES) - 2  # allow a slip on the weakest driver


def test_distressed_riskier_than_healthy():
    _, results, fitted, scaler = _fit_small()
    best = fitted[results.iloc[0]["model"]]
    pd_h = score_firm(best, scaler, HEALTHY, horizons=(5,))["pd"]["5y"]
    pd_d = score_firm(best, scaler, DISTRESSED, horizons=(5,))["pd"]["5y"]
    assert pd_d > pd_h
    assert pd_d > 0.10


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
