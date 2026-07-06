"""Monte Carlo sanity tests: reproducibility, moments, correlation sign."""

from __future__ import annotations

import numpy as np

from app.fulcrum import SimConfig, simulate_enterprise_value


def test_reproducible_and_shapes():
    cfg = SimConfig(base_ebitda=100, n_draws=20_000, seed=1)
    a, b = simulate_enterprise_value(cfg), simulate_enterprise_value(cfg)
    assert np.array_equal(a.ev, b.ev)
    assert a.ev.shape == (20_000,)
    assert (a.ev >= 0).all()


def test_no_stress_multiple_mean_is_base():
    # With stress off, E[multiple] should equal base_multiple (lognormal mean-corrected).
    cfg = SimConfig(base_ebitda=100, stress_prob=0.0, base_multiple=6.0, n_draws=200_000, seed=2)
    out = simulate_enterprise_value(cfg)
    assert abs(out.multiple.mean() - 6.0) < 0.05
    assert not out.in_stress.any()


def test_stress_lowers_ev():
    base = SimConfig(base_ebitda=100, stress_prob=0.0, n_draws=100_000, seed=3)
    stressed = SimConfig(base_ebitda=100, stress_prob=1.0, n_draws=100_000, seed=3)
    assert simulate_enterprise_value(stressed).ev.mean() < simulate_enterprise_value(base).ev.mean()


def test_correlation_sign():
    cfg = SimConfig(base_ebitda=100, corr=0.8, stress_prob=0.0, n_draws=100_000, seed=4)
    out = simulate_enterprise_value(cfg)
    r = np.corrcoef(np.log(out.ebitda), np.log(out.multiple))[0, 1]
    assert r > 0.7
