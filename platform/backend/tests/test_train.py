"""Trained hazard model: monotone in balance-sheet risk, blind to market data, embargoed."""
from __future__ import annotations

from functools import lru_cache
from types import SimpleNamespace

import numpy as np

from app.hazard import train
from app.hazard.score import TrainedHazardScorer


@lru_cache(maxsize=1)
def _scorer() -> TrainedHazardScorer:
    """Fitted on a synthetic panel with the real panel's two missingness traps."""
    df = train._synthetic_panel(n_firms=400, years=6, seed=4)
    rng = np.random.default_rng(0)
    # debt/EBITDA is undefined at EBITDA <= 0 (distressed) and where a filing lacks the
    # inputs (mostly healthy filers)
    nonpos = (df["roa"] < 0).to_numpy()
    gap = ~nonpos & (rng.uniform(size=len(df)) < 0.7)
    df["ebitda_nonpositive"] = np.where(gap, np.nan, nonpos.astype(float))
    df.loc[nonpos | gap, "net_debt_to_ebitda"] = np.nan
    # defaulters rarely have a price history, survivors usually do
    priced = rng.uniform(size=len(df)) < np.where(df["label"] == 1, 0.07, 0.5)
    for col, v in (("equity_vol", 0.4), ("drawdown_52w", -0.2), ("excess_return_1y", 0.0)):
        df[col] = np.where(priced, v + 0.1 * rng.standard_normal(len(df)), np.nan)
    return TrainedHazardScorer(train.train_from_panel(df, save=False)[1])


_FIRM = {"leverage": 0.6, "net_debt_to_ebitda": 4.0, "ebitda_nonpositive": 0.0,
         "interest_coverage": 2.0, "current_ratio": 1.2, "quick_ratio": 0.8, "cash_ratio": 0.1,
         "roa": 0.01, "fcf_margin": 0.0, "wc_to_assets": 0.05, "re_to_assets": 0.0,
         "size_log_assets": 7.0, "runway_years": None}


def test_market_data_never_moves_pd():
    # trained on market features, the model reads "has a price history" as safety
    s = _scorer()
    no_tape = s.score(_FIRM)["value"]
    for vol, dd, xr in ((0.4, -0.2, 0.0), (0.9, -0.7, -0.6)):     # typical, distressed
        tape = SimpleNamespace(ok=True, equity_vol=vol, drawdown_52w=dd, excess_return_1y=xr)
        assert s.score(_FIRM, tape)["value"] == no_tape


def test_pd_never_falls_as_balance_sheet_worsens_through_ebitda_zero():
    path = [{**_FIRM, "leverage": lev, "net_debt_to_ebitda": nde, "interest_coverage": cov}
            for lev, nde, cov in ((0.3, 1.0, 8.0), (0.6, 4.0, 2.0), (0.8, 9.0, 0.8),
                                  (0.8, 60.0, 0.1))]
    # EBITDA turns negative; nothing else moves
    path.append({**path[-1], "net_debt_to_ebitda": None, "ebitda_nonpositive": 1.0})
    pds = [_scorer().score(f)["value"] for f in path]
    assert pds == sorted(pds), pds


def test_walk_forward_embargo_drops_the_year_before_each_test_year(monkeypatch):
    df = train._synthetic_panel(n_firms=200, years=4, seed=1)    # FY2016-FY2019
    fits, real_fit = [], train._fit

    def spy(X, y):
        fits.append(len(y))
        return real_fit(X, y)

    monkeypatch.setattr(train, "_fit", spy)
    ev = train.walk_forward_eval(df)
    # 2017 has no fiscal year a full year back; 2018 trains on FY2016 alone, 2019 on FY2016-17
    assert sorted(ev["auc_by_year"]) == [2018, 2019]
    assert fits == [200, 400]
