"""End-to-end recovery outputs: table columns, fulcrum identification."""

from __future__ import annotations

import numpy as np

from app.fulcrum import CapitalStructure, Entity, SimConfig, Tranche, analyze

REQUIRED_COLUMNS = {
    "tranche", "entity", "face", "mean_recovery_%", "mean_recovery_$",
    "median_recovery_%", "p10_%", "p90_%", "lgd_%", "prob_impaired_%",
    "prob_full_%", "prob_zero_%", "is_fulcrum",
}


def _structure() -> CapitalStructure:
    return CapitalStructure(
        name="T",
        entities=[Entity("Co", ev_share=1.0)],
        tranches=[
            Tranche("1L", "Co", 500, lien_rank=1, secured=True),
            Tranche("2L", "Co", 250, lien_rank=2, secured=True),
            Tranche("Unsec", "Co", 120),
        ],
    )


def test_table_columns_and_consistency():
    result = analyze(_structure(), SimConfig(base_ebitda=120, n_draws=20_000, seed=5))
    df = result.table()
    assert REQUIRED_COLUMNS <= set(df.columns)
    assert len(df) == 3
    # LGD is the complement of mean recovery; impairment is the complement of full.
    assert np.allclose(df["lgd_%"], 100 - df["mean_recovery_%"])
    assert np.allclose(df["prob_impaired_%"], 100 - df["prob_full_%"])
    # Priority order: senior recovers no less than junior on average.
    assert df["mean_recovery_%"].is_monotonic_decreasing


def test_fulcrum_is_first_impaired_class():
    # Near-deterministic EV ~ 120 * 5.0 = 600 covers the 1L (500) but breaks in the 2L.
    cfg = SimConfig(
        base_ebitda=120, base_multiple=5.0, stress_prob=0.0,
        ebitda_vol=0.01, multiple_vol=0.01, n_draws=5_000, seed=6,
    )
    result = analyze(_structure(), cfg)
    assert result.fulcrum == "2L"
    assert result.table().set_index("tranche")["is_fulcrum"]["2L"]
