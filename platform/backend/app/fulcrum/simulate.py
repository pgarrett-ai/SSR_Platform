"""Enterprise-value Monte Carlo.

Enterprise value at emergence is the product of two random legs that are *not*
independent - in a real downturn EBITDA misses and multiple compression happen
together:

    EV = terminal_EBITDA  x  exit_multiple

terminal_EBITDA
    Log-EBITDA follows a mean-reverting (Ornstein-Uhlenbeck) process around the
    base-case forecast. A "stress" regime (drawn per path) widens the volatility
    and adds a negative drift, so the distribution has a fat, heavy left tail
    rather than a symmetric one.

exit_multiple
    Drawn from a lognormal around a base multiple, with the *mean* multiple
    compressed in the stress regime (distressed exits clear at lower multiples
    than the LBO-era entry multiple).

correlation
    A Gaussian copula links the EBITDA shock and the multiple shock so the two
    legs move together. corr=0 recovers the naive independent case.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class SimConfig:
    # --- EBITDA leg ---
    base_ebitda: float                  # base-case (point-estimate) EBITDA, $mm
    horizon_years: float = 1.0          # time to emergence / exit
    ebitda_vol: float = 0.25            # annualized log-vol, normal regime
    mean_reversion: float = 0.5         # OU speed (kappa), per year; higher = tighter
    # --- stress regime ---
    stress_prob: float = 0.25           # probability a path is in the stress regime
    stress_vol: float = 0.55            # annualized log-vol in stress
    stress_log_drift: float = -0.30     # additive drift to log-EBITDA over the horizon in stress
    # --- multiple leg ---
    base_multiple: float = 6.0          # normal-regime mean exit multiple (x EBITDA)
    distress_multiple: float = 4.5      # stress-regime mean exit multiple (compressed)
    multiple_vol: float = 0.18          # lognormal vol of the multiple
    # --- claims ---
    accrual_years: float = 0.0          # coupon-periods of accrued interest in the allowed claim
                                        # (0 = principal-only claims; UI defaults to 0.25)
    # --- coupling & sampling ---
    corr: float = 0.5                   # correlation between EBITDA and multiple shocks
    n_draws: int = 50_000
    seed: int = 42

    def __post_init__(self) -> None:
        # isfinite first: NaN evades a bare `<= 0` (all NaN comparisons are False) and would
        # otherwise poison the whole simulation with NaN
        if not math.isfinite(self.base_ebitda) or self.base_ebitda <= 0:
            raise ValueError("base_ebitda must be a positive finite number")
        if self.accrual_years < 0:
            raise ValueError("accrual_years must be non-negative")
        if not -1.0 <= self.corr <= 1.0:
            raise ValueError("corr must be in [-1, 1]")
        if not 0.0 <= self.stress_prob <= 1.0:
            raise ValueError("stress_prob must be in [0, 1]")
        if self.mean_reversion <= 0:
            raise ValueError("mean_reversion (kappa) must be positive")
        if not 1 <= self.n_draws <= 1_000_000:   # request-derived: cap the RNG/array allocation
            raise ValueError("n_draws must be in [1, 1_000_000]")


@dataclass
class SimOutput:
    ev: np.ndarray            # (N,) enterprise value draws
    ebitda: np.ndarray        # (N,) terminal EBITDA draws
    multiple: np.ndarray      # (N,) exit-multiple draws
    in_stress: np.ndarray     # (N,) bool regime flag


def _ou_terminal_std(sigma: np.ndarray, kappa: float, t: float) -> np.ndarray:
    """Std. dev. of an OU process' terminal value (mean-reverting to 0).

    Var = sigma^2 / (2 kappa) * (1 - exp(-2 kappa t)).
    """
    return sigma * np.sqrt((1.0 - np.exp(-2.0 * kappa * t)) / (2.0 * kappa))


def simulate_enterprise_value(cfg: SimConfig) -> SimOutput:
    """Draw a vector of enterprise values under the model above."""
    rng = np.random.default_rng(cfg.seed)
    n = cfg.n_draws

    # Regime draw per path.
    in_stress = rng.random(n) < cfg.stress_prob

    # Correlated standard-normal shocks via a Gaussian copula.
    z1 = rng.standard_normal(n)                       # drives EBITDA
    z_ind = rng.standard_normal(n)
    z2 = cfg.corr * z1 + np.sqrt(max(1.0 - cfg.corr**2, 0.0)) * z_ind  # drives multiple

    # --- EBITDA leg (OU terminal, regime-dependent vol + drift) ---
    sigma = np.where(in_stress, cfg.stress_vol, cfg.ebitda_vol)
    std = _ou_terminal_std(sigma, cfg.mean_reversion, cfg.horizon_years)
    drift = np.where(in_stress, cfg.stress_log_drift, 0.0)
    log_dev = drift + std * z1                        # log deviation from base
    ebitda = cfg.base_ebitda * np.exp(log_dev)
    ebitda = np.maximum(ebitda, 0.0)

    # --- multiple leg (lognormal, regime-dependent mean) ---
    mean_mult = np.where(in_stress, cfg.distress_multiple, cfg.base_multiple)
    v = cfg.multiple_vol
    # E[mult] = mean_mult exactly, since E[exp(v z - v^2/2)] = 1.
    multiple = mean_mult * np.exp(v * z2 - 0.5 * v**2)
    multiple = np.maximum(multiple, 0.0)

    ev = np.maximum(ebitda * multiple, 0.0)
    return SimOutput(ev=ev, ebitda=ebitda, multiple=multiple, in_stress=in_stress)
