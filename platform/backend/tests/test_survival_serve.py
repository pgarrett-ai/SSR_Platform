"""Tests for the lazy survival-panel serving seam (survival/serve.py) and the offline
fit script (survival/fit.py) — wiring the orphaned 5-model survival package into the
hazard pipeline / RiskPage.

Run with `pytest tests/test_survival_serve.py`.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.hazard import train
from app.hazard.survival import fit as survival_fit
from app.hazard.survival import serve as survival_serve

# Renamed from test_survival.py's HEALTHY/DISTRESSED: retained_earnings_to_assets ->
# re_to_assets — this is the real-panel feature naming that pipeline.py's `latest`
# dict (and therefore `survival_panel`'s `feats` argument) actually uses.
HEALTHY_FEATS = {
    "size_log_assets": 8.5, "leverage": 0.20, "roa": 0.12, "interest_coverage": 12.0,
    "current_ratio": 2.2, "cash_ratio": 0.18, "re_to_assets": 0.40, "wc_to_assets": 0.30,
}
DISTRESSED_FEATS = {
    "size_log_assets": 6.2, "leverage": 0.80, "roa": -0.02, "interest_coverage": 1.6,
    "current_ratio": 0.95, "cash_ratio": 0.04, "re_to_assets": -0.05, "wc_to_assets": 0.00,
}


@pytest.fixture(autouse=True)
def _clear_bundle_cache():
    survival_serve._load_bundle.cache_clear()
    yield
    survival_serve._load_bundle.cache_clear()


def _truncate_at_default(df):
    """train._synthetic_panel gives every firm the identical fixed-length calendar span
    (no early exit at default), so raw firm-level duration is a constant across the whole
    panel — which breaks evaluate.time_grid's bracket (needs event-time spread). Real
    panels (labels.build_real_panel) already stop a defaulter's rows at the first Item 1.03
    proximate year; mirror that here so the fixture has the duration variance production
    data has."""
    df = df.sort_values(["firm_id", "date"]).reset_index(drop=True)
    prior_cum = df.groupby("firm_id")["label"].cumsum() - df["label"]
    return df[prior_cum == 0].reset_index(drop=True)


def _fit_tmp_bundle(tmp_path, monkeypatch, df, label_source=None):
    path = tmp_path / "survival_panel.joblib"
    monkeypatch.setattr(survival_fit, "SURVIVAL_PATH", path)
    bundle = survival_fit.fit_survival_bundle(df, label_source=label_source, save=True)
    survival_serve._load_bundle.cache_clear()
    return bundle


def test_bundle_absent_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(survival_fit, "SURVIVAL_PATH", tmp_path / "nope.joblib")
    survival_serve._load_bundle.cache_clear()
    assert survival_serve.survival_panel(DISTRESSED_FEATS) is None


def test_round_trip_accounting_only(tmp_path, monkeypatch):
    df = _truncate_at_default(train._synthetic_panel(n_firms=200, years=6, seed=1))
    bundle = _fit_tmp_bundle(tmp_path, monkeypatch, df, label_source="unit-test fixture")

    # Coverage-drop: the synthetic panel has no market columns at all, so equity_vol/
    # excess_return never clear the >=50% coverage bar — same reason sales_growth (never
    # in FIT_FEATURES) is absent. This is what lets this test reuse train._synthetic_panel.
    assert "equity_vol" not in bundle["features"]
    assert "excess_return" not in bundle["features"]
    assert "sales_growth" not in bundle["features"]

    p_healthy = survival_serve.survival_panel(HEALTHY_FEATS)
    p_distressed = survival_serve.survival_panel(DISTRESSED_FEATS)
    assert p_healthy is not None and p_distressed is not None
    assert len(p_healthy["models"]) == 5
    assert len(p_distressed["models"]) == 5
    for row in p_healthy["models"] + p_distressed["models"]:
        assert 0.0 <= row["pd_1y"] <= 1.0
        assert row["c_index"] is not None and row["c_index"] > 0.5

    mean_h = np.mean([r["pd_1y"] for r in p_healthy["models"]])
    mean_d = np.mean([r["pd_1y"] for r in p_distressed["models"]])
    assert mean_d > mean_h


def test_panel_to_survival_shape():
    df = train._synthetic_panel(n_firms=50, years=5, seed=2)
    feats = ["size_log_assets", "leverage", "roa"]
    X, names, y = survival_fit.panel_to_survival(df, feats)
    n_firms = df["firm_id"].nunique()
    assert X.shape == (n_firms, len(feats))
    assert len(y) == n_firms
    expected_events = int((df.groupby("firm_id")["label"].max() == 1).sum())
    assert int(y["event"].sum()) == expected_events


def test_market_features_flow_through_serve(tmp_path, monkeypatch):
    # critique #6 (binding): the market-feature serve path must be exercised, not just the
    # accounting path — that's the only reason survival_panel() takes a `market` argument.
    df = train._synthetic_panel(n_firms=200, years=6, seed=1)
    rng = np.random.default_rng(0)
    df["equity_vol"] = np.clip(0.3 + 0.1 * rng.standard_normal(len(df)), 0.05, 2.0)
    df["excess_return_1y"] = 0.05 * rng.standard_normal(len(df))
    df = _truncate_at_default(df)

    bundle = _fit_tmp_bundle(tmp_path, monkeypatch, df)
    assert "equity_vol" in bundle["features"]
    assert "excess_return" in bundle["features"]

    market = SimpleNamespace(ok=True, equity_vol=0.6, excess_return_1y=-0.3)
    with_market = survival_serve.survival_panel(DISTRESSED_FEATS, market=market)
    without_market = survival_serve.survival_panel(DISTRESSED_FEATS, market=None)
    assert with_market is not None and without_market is not None

    with_vals = [r["pd_1y"] for r in with_market["models"]]
    without_vals = [r["pd_1y"] for r in without_market["models"]]
    # Market values actually flowed into build_model_matrix -> different model input ->
    # different output. If they were silently NaN-imputed away in both calls, these would
    # be identical.
    assert with_vals != without_vals


def test_pipeline_import_stays_lazy_without_bundle():
    """Binding laziness contract: importing app.hazard.pipeline (which imports serve.py at
    module top) must not pull in sksurv/lifelines when no bundle is on disk. Runs in a
    fresh subprocess — sys.modules is process-global and other tests in this session
    already import sksurv/lifelines directly, which would give a false pass in-process."""
    import subprocess
    import sys

    assert not survival_fit.SURVIVAL_PATH.exists(), (
        "a real bundle on disk would invalidate this check — this session must not run "
        "the real fit script against panel.db"
    )

    code = (
        "import sys\n"
        "import app.hazard.pipeline\n"
        "heavy = sorted(m for m in sys.modules if m.startswith(('sksurv', 'lifelines')))\n"
        "assert not heavy, f'heavy modules loaded with no bundle present: {heavy}'\n"
        "print('OK')\n"
    )
    backend_dir = str(Path(__file__).resolve().parent.parent)
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=backend_dir, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr
