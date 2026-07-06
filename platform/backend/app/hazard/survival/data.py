"""Synthetic corporate-credit panel with a known data-generating process.

Real default modeling needs a labeled panel (e.g., Compustat/CRSP fundamentals
joined to a bankruptcy database such as the UCLA-LoPucki BRD). That data is
licensed, so this module ships a *realistic synthetic generator* instead: firms
are drawn with a latent credit-quality factor that drives correlated financial
ratios, and time-to-default is sampled from a **Weibull proportional-hazards**
model with known coefficients. Because the true betas are known, we can confirm
the fitted models recover the right risk drivers (sign and ranking) - a built-in
correctness check you don't get with real data.

To use real data instead, produce a DataFrame with the columns in
`features.FEATURES` plus `firm_id`, `duration` (years observed), and `event`
(1 = defaulted, 0 = censored), and feed it straight into the same pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .features import FEATURES, build_model_matrix

# True standardized log-hazard coefficients (sign = economic intuition).
# Positive => raises default hazard; negative => protective.
TRUE_BETA: dict[str, float] = {
    "size_log_assets": -0.35,
    "leverage": 0.55,
    "roa": -0.60,
    "interest_coverage": -0.45,   # applied to log(coverage)
    "current_ratio": -0.30,       # applied to log(current_ratio)
    "cash_ratio": -0.25,
    "retained_earnings_to_assets": -0.45,
    "wc_to_assets": -0.20,
    "sales_growth": -0.25,
    "equity_vol": 0.50,           # applied to log(equity_vol)
    "excess_return": -0.40,
}


@dataclass
class PanelConfig:
    n_firms: int = 5000
    horizon: float = 8.0          # administrative censoring (max years observed)
    weibull_shape: float = 1.3    # >1 => default hazard rises with time
    baseline_scale: float = 0.013 # baseline annual hazard level (tunes default rate)
    other_exit_rate: float = 0.06 # annual hazard of non-default exit (M&A/delisting) -> censoring
    seed: int = 11


def _draw_features(rng: np.random.Generator, n: int) -> pd.DataFrame:
    """Draw correlated, realistic financial ratios from a latent quality factor."""
    q = rng.standard_normal(n)  # latent credit quality (higher = healthier)

    def mix(base, q_load, noise_sd):
        return base + q_load * q + noise_sd * rng.standard_normal(n)

    df = pd.DataFrame(
        {
            "size_log_assets": mix(7.0, 0.6, 0.9),
            "leverage": np.clip(mix(0.45, -0.10, 0.18), 0.01, 1.6),
            "roa": mix(0.04, 0.05, 0.06),
            "interest_coverage": np.clip(np.exp(mix(1.3, 0.55, 0.6)), 0.05, 5000.0),
            "current_ratio": np.clip(np.exp(mix(0.4, 0.20, 0.30)), 0.1, 20.0),
            "cash_ratio": np.clip(mix(0.08, 0.03, 0.05), 0.0, 0.7),
            "retained_earnings_to_assets": mix(0.10, 0.20, 0.22),
            "wc_to_assets": mix(0.12, 0.10, 0.18),
            "sales_growth": mix(0.04, 0.06, 0.15),
            "equity_vol": np.clip(np.exp(mix(-1.0, -0.25, 0.35)), 0.05, 3.0),
            "excess_return": mix(0.0, 0.10, 0.30),
        }
    )
    return df


def generate_panel(cfg: PanelConfig | None = None) -> pd.DataFrame:
    """Generate a firm-level survival dataset.

    Returns a DataFrame with one row per firm: the FEATURES columns plus
    `firm_id`, `duration` (years until default or censoring) and `event`
    (1 = default).
    """
    cfg = cfg or PanelConfig()
    rng = np.random.default_rng(cfg.seed)
    n = cfg.n_firms

    df = _draw_features(rng, n)

    # Linear risk index on standardized (log-transformed) features.
    X, names = build_model_matrix(df)
    Xz = (X - X.mean(axis=0)) / X.std(axis=0)
    beta = np.array([TRUE_BETA[f] for f in FEATURES])  # names align to FEATURES order
    eta = Xz @ beta
    eta = eta - eta.mean()  # center so baseline_scale controls the overall rate

    # Weibull proportional-hazards inverse sampling.
    # H(t) = baseline_scale * t**k * exp(eta);  S(t) = exp(-H(t))
    # => t_default = ( -ln(U) / (baseline_scale * exp(eta)) ) ** (1/k)
    k = cfg.weibull_shape
    u = rng.uniform(size=n)
    t_default = (-np.log(u) / (cfg.baseline_scale * np.exp(eta))) ** (1.0 / k)

    # Competing non-default exit (exponential) -> produces censoring.
    t_other = rng.exponential(1.0 / cfg.other_exit_rate, size=n)

    # Administrative censoring at the horizon.
    censor_time = np.minimum(t_other, cfg.horizon)

    event = (t_default <= censor_time).astype(int)
    duration = np.minimum(t_default, censor_time)

    df["firm_id"] = np.arange(n)
    df["duration"] = duration
    df["event"] = event
    return df
