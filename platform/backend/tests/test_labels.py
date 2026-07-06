"""Phase 5: real default labels — label-window logic + bundle provenance (no network)."""
from __future__ import annotations

import datetime as dt

from app.hazard.labels import annual_default_rate, label_for_year, ten_k_ciks
from app.hazard.train import _synthetic_panel, prior_correct, train_from_panel
from app.hazard.score import TrainedHazardScorer, implied_rating

D = dt.date


def test_label_window():
    ev = D(2024, 6, 15)
    assert label_for_year(D(2023, 12, 31), ev) == 1          # default 167d after FY end
    assert label_for_year(D(2022, 12, 31), ev) == 0          # >365d before the event
    assert label_for_year(D(2024, 12, 31), ev) is None       # post-petition FY -> dropped
    assert label_for_year(D(2024, 6, 15), ev) is None        # FY end ON the event day
    assert label_for_year(D(2023, 6, 16), ev) == 1           # exactly 365d -> inside horizon
    assert label_for_year(D(2023, 12, 31), None) == 0        # control firm


def test_ten_k_ciks_parses_form_idx():
    idx = "\n".join([
        "Form Type   Company Name                  CIK     Date Filed  File Name",
        "-" * 90,
        "10-K        DEAD AIRLINE HOLDINGS INC     1234567 2016-02-26  edgar/data/1234567/x.txt",
        "10-K        SOLO                          0000042 2016-03-01  edgar/data/42/y.txt",
        "10-K/A      AMENDED CORP                  7654321 2016-04-11  edgar/data/7654321/z.txt",
        "10-Q        QUARTERLY ONLY INC            1112223 2016-05-02  edgar/data/1112223/q.txt",
        "10-K        BAD LINE NO CIK HERE",
    ])
    assert ten_k_ciks(idx) == {"1234567", "42"}    # exact 10-K only, zeros stripped


def test_prior_correction_and_implied_rating():
    # identity when sample rate == true rate
    assert abs(prior_correct(0.30, 0.30, 0.30) - 0.30) < 1e-9
    # case-control 9% sample vs 0.5% real world: PD must shrink, order preserved
    lo, hi = prior_correct(0.05, 0.09, 0.005), prior_correct(0.40, 0.09, 0.005)
    assert lo < 0.05 and hi < 0.40 and lo < hi
    # measured base rate: 2 events over 400 firm-years
    uni = {"1": [2015, 2018], "2": [2015, 2016]}          # 4 + 2 firm-years
    assert abs(annual_default_rate([{}, {}], uni) - 2 / 6) < 1e-9
    # agency bands: nearest in log-space
    assert implied_rating(0.0001) == "AAA"
    assert implied_rating(0.005) == "BB"
    assert implied_rating(0.03) == "B"
    assert implied_rating(0.30) == "CCC/C"


def test_bundle_meta_and_real_labels_flag():
    df = _synthetic_panel(n_firms=80, years=4, seed=3)
    _, bundle = train_from_panel(df, save=False, meta={"label_source": "8-K Item 1.03 test"})
    s = TrainedHazardScorer(bundle)
    sc = s.score({f: 0.5 for f in bundle["features"]})
    assert sc["real_labels"] is True
    assert "8-K Item 1.03 test" in sc["note"]

    _, demo = train_from_panel(df, save=False)               # no meta -> demo provenance
    sc2 = TrainedHazardScorer(demo).score({f: 0.5 for f in demo["features"]})
    assert sc2["real_labels"] is False
    assert "demo-trained" in sc2["note"]
