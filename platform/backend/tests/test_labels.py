"""Phase 5: real default labels — label-window logic + bundle provenance (no network)."""
from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pandas as pd

import app.hazard.labels as labels
from app.hazard.labels import (annual_default_rate, label_for_year, norm_name,
                               sd_events_from_frame, ten_k_ciks)
from app.hazard.market import pit_market_features
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


def test_sd_events_from_frame():
    frame = pd.DataFrame([
        # issuer-level D with CIK in the file -> taken as-is
        {"obligor_name": '"Alpha Airways, Inc."', "central_index_key": "0001234",
         "rating": "D", "rating_action_date": "2020-05-01",
         "rating_type": "Long Term Issuer Default Rating"},
        # later RD for the same obligor -> first event wins
        {"obligor_name": '"Alpha Airways, Inc."', "central_index_key": "0001234",
         "rating": "RD", "rating_action_date": "2021-03-01",
         "rating_type": "Long Term Issuer Default Rating"},
        # no CIK -> matched by normalized name
        {"obligor_name": '"Beta Retail Corp"', "central_index_key": None,
         "rating": "RD", "rating_action_date": "2019-01-15",
         "rating_type": "Long Term Issuer Default Rating"},
        # foreign issuer, no CIK and no match -> counted unmatched
        {"obligor_name": '"Gamma GmbH"', "central_index_key": None,
         "rating": "D", "rating_action_date": "2018-02-02",
         "rating_type": "Long Term Issuer Default Rating"},
        # instrument-level rating -> ignored
        {"obligor_name": '"Alpha Airways, Inc."', "central_index_key": "0001234",
         "rating": "D", "rating_action_date": "2017-01-01",
         "rating_type": "Long Term Rating"},
        # non-default rating -> ignored
        {"obligor_name": '"Delta Corp"', "central_index_key": "0009999",
         "rating": "B", "rating_action_date": "2020-01-01",
         "rating_type": "Long Term Issuer Default Rating"},
    ])
    events, unmatched = sd_events_from_frame(frame, {"BETA RETAIL": "777"})
    assert unmatched == 1
    assert [(e["cik"], e["filed"]) for e in events] == [("777", "2019-01-15"),
                                                        ("1234", "2020-05-01")]
    assert all(e["source"] == "fitch_rd" for e in events)
    assert norm_name('"Beta Retail Corp"'.strip('"')) == "BETA RETAIL"


def test_sd_events_from_frame_moodys_config():
    frame = pd.DataFrame([
        # organization-level C -> default event
        {"obligor_name": '"Alpha Airways, Inc."', "central_index_key": "0001234",
         "rating": "C", "rating_action_date": "2020-05-01",
         "rating_type": "Organization"},
        # Ca is "very near default", not default -> ignored
        {"obligor_name": '"Beta Retail Corp"', "central_index_key": "0000777",
         "rating": "Ca", "rating_action_date": "2019-01-15",
         "rating_type": "Organization"},
        # instrument-level C -> ignored (type must equal Organization)
        {"obligor_name": '"Delta Corp"', "central_index_key": "0009999",
         "rating": "C", "rating_action_date": "2020-01-01",
         "rating_type": "Instrument"},
    ])
    events, unmatched = sd_events_from_frame(frame, {}, ratings={"C"},
                                             type_pattern="^Organization$",
                                             source="moodys_c")
    assert unmatched == 0
    assert [(e["cik"], e["source"]) for e in events] == [("1234", "moodys_c")]


def test_panel_store_checkpoints_and_resumes(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(cik, event_date, lookback, horizon_days):
        calls.append(cik)
        if cik == "9":
            raise ConnectionError("transient")
        label = 1 if event_date else 0
        return [{"firm_id": cik, "date": "2019-12-31", "label": label, "leverage": 0.5},
                {"firm_id": cik, "date": "2018-12-31", "label": 0, "leverage": 0.4}]

    monkeypatch.setattr(labels, "PANEL_DB", tmp_path / "panel.db")
    monkeypatch.setattr(labels, "_fetch_firm_rows", fake_fetch)
    monkeypatch.setattr(labels, "load_or_harvest_universe",
                        lambda *a, **k: {"2": [2015, 2026], "9": [2015, 2026],
                                         "3": [2015, 2026]})
    events = [{"cik": "1", "filed": "2020-06-01"}]

    df1 = labels.build_real_panel(events, n_defaulters=1, n_controls=3, start_year=2015)
    # 1 defaulter + controls 2 and 3 + the failing control 9, each fetched once
    assert sorted(calls) == ["1", "2", "3", "9"]
    assert len(df1) == 6 and int(df1["label"].sum()) == 1

    df2 = labels.build_real_panel(events, n_defaulters=1, n_controls=3, start_year=2015)
    # successes served from the store; only the FAILED firm is retried
    assert sorted(calls) == ["1", "2", "3", "9", "9"]
    assert len(df2) == len(df1) and list(df2.columns) == list(df1.columns)


def test_sd_merge_keeps_earliest_event(tmp_path, monkeypatch):
    ev_path, sd_path = tmp_path / "ev.json", tmp_path / "sd.json"
    ev_path.write_text(json.dumps([
        {"cik": "1", "name": "A", "filed": "2020-06-01"},
        {"cik": "2", "name": "B", "filed": "2021-01-01"}]))
    sd_path.write_text(json.dumps([
        {"cik": "1", "name": "A", "filed": "2019-03-01", "source": "fitch_rd"},   # earlier -> wins
        {"cik": "3", "name": "C", "filed": "2022-07-01", "source": "fitch_rd"}])) # new CIK -> added
    monkeypatch.setattr(labels, "EVENTS_PATH", ev_path)
    monkeypatch.setattr(labels, "SD_PATH", sd_path)
    merged = labels.load_or_harvest_events()
    assert [(e["cik"], e["filed"]) for e in merged] == [
        ("1", "2019-03-01"), ("2", "2021-01-01"), ("3", "2022-07-01")]


def test_pit_market_features_no_lookahead():
    # 2 years of synthetic daily prices: flat 100 through 2019, then a crash to 50 in 2020
    idx = pd.bdate_range("2019-01-01", "2020-12-31")
    px = pd.Series(100.0, index=idx)
    px.loc["2020-07-01":] = 50.0
    bench = pd.Series(100.0, index=idx)

    before = pit_market_features(px, D(2019, 12, 31), bench)
    after = pit_market_features(px, D(2020, 12, 31), bench)
    assert before["drawdown_52w"] == 0.0                  # crash not visible in 2019 window
    assert after["drawdown_52w"] < -0.45                  # visible after it happened
    assert before["equity_vol"] == 0.0 and after["equity_vol"] > 0.0
    assert after["excess_return_1y"] < -0.45              # -50% vs flat benchmark
    # stale-history guard: prices stop months before period_end -> all None
    stale = pit_market_features(px.loc[:"2020-03-31"], D(2020, 12, 31), bench)
    assert all(v is None for v in stale.values())
    none = pit_market_features(None, D(2020, 12, 31))
    assert all(v is None for v in none.values())


def test_walk_forward_eval_reports_operating_points():
    from app.hazard.train import walk_forward_eval
    df = _synthetic_panel(n_firms=150, years=6, seed=1)
    ev = walk_forward_eval(df)
    assert ev["auc_by_year"], "expected at least one walk-forward fold"
    ops = ev["operating_points"]
    assert set(ops) == {"top_5pct", "top_10pct"}
    for op in ops.values():
        assert 0.0 <= op["precision"] <= 1.0 and op["n_flagged"] >= 1
    assert len(ev["calibration"]) == 10
    # deciles are rank-ordered: realized default rate should not collapse at the top
    assert ev["calibration"][-1]["mean_pred"] >= ev["calibration"][0]["mean_pred"]


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


def test_sign_safe_panel_backfills_stale_checkpoint_rows():
    # panel.db rows fetched before the C3 fix carry a negative net_debt_to_ebitda and no
    # runway_years — build_real_panel recomputes both from the cached raw levels.
    import pytest

    df = pd.DataFrame([
        {"ebitda": -2.1e9, "total_debt": 2.0e9, "cash": 0.5e9, "fcf": -3.0e9,
         "net_debt_to_ebitda": -0.71},
        {"ebitda": 1.2e9, "total_debt": 2.0e9, "cash": 0.5e9, "fcf": 1.0e9,
         "net_debt_to_ebitda": 1.25},
        {"ebitda": 1.0e9, "total_debt": np.nan, "cash": np.nan, "fcf": np.nan,
         "net_debt_to_ebitda": np.nan},
    ])
    out = labels.sign_safe_panel(df)
    assert np.isnan(out.loc[0, "net_debt_to_ebitda"])
    assert out.loc[0, "runway_years"] == pytest.approx(0.5 / 3.0)
    assert out.loc[1, "net_debt_to_ebitda"] == pytest.approx(1.5 / 1.2)
    assert np.isnan(out.loc[1, "runway_years"])
    assert np.isnan(out.loc[2, "net_debt_to_ebitda"])


def test_runway_feature_registered_with_monotone_sign():
    from app.hazard import train
    assert "runway_years" in train.TRAIN_FEATURES
    assert train.MONOTONE["runway_years"] == -1
    assert all(f in train.MONOTONE for f in train.TRAIN_FEATURES)  # _fit indexes MONOTONE[f]
