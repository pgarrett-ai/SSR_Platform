"""Refi-wall sequencing (Moyer ch. 6/10): conditional-PD identity, sequential funding,
back-to-front verdicts, refi-prob anchoring, markets-closed threshold, degradation."""
import datetime as dt
import json

import pytest

from app.capstack import refi
from app.capstack.refi import (build_refi_wall, conditional_from_cum, conditional_pds,
                               hazard_inputs, sequence_walls)


def _cv(v):
    return {"value": v, "derived": True, "formula": "test"}


def test_conditional_identity():
    # (0.20 − 0.10) / (1 − 0.10) = 0.1111…
    out = conditional_from_cum([0.10, 0.20])
    assert out[0] == 0.10
    assert out[1] == pytest.approx(0.111111, abs=1e-6)
    # a dipping risk-neutral cum PD floors at 0, never goes negative
    assert conditional_from_cum([0.10, 0.09]) == [0.10, 0.0]


def test_merton_horizon_extension_monotone():
    # LCID-shaped distressed profile: cumulative PD non-decreasing over near horizons,
    # conditionals all >= 0 (floored — a risk-neutral cum PD can dip far out)
    out = conditional_pds(E=2.5e9, sigma_E=0.85, D=2.7e9, r=0.04,
                          horizons=(0.5, 1.0, 2.0, 4.0))
    cum = out["cum"]
    assert all(b >= a - 1e-12 for a, b in zip(cum, cum[1:]))
    assert all(c >= 0 for c in out["conditional"])
    assert out["conditional"][0] == cum[0]


def test_sequential_funding_hand_checked():
    # liq 100, sweep 50/yr, walls 120 @ y1 and 200 @ y3:
    #   y1: resources 100+50 = 150 -> covered; y3: 100+150−120 = 130 -> need 70
    ladder = [{"year": 2027, "face_mm": 120.0, "instruments": []},
              {"year": 2029, "face_mm": 200.0, "instruments": []}]
    rows = sequence_walls(ladder, 100.0, [50.0, 100.0, 150.0], 2026)
    assert rows[0]["refi_need_mm"] == 0.0
    assert rows[0]["repayable_mm"] == 120.0
    assert rows[1]["resources_mm"] == 130.0
    assert rows[1]["refi_need_mm"] == 70.0


def _ov(wall, schedule=None, ebitda=-10e6, liquidity=None):
    ov = {
        "header": {"issuer": "Test Co", "ticker": "ZZREFI"},
        "debt_schedule_asof": "2026-06-30",
        "economic_debt_bridge": {"ebitda": _cv(ebitda)},
        "forensic_table": [{"fiscal_year": 2025, "ebitda": _cv(ebitda)}],
        "debt_schedule": schedule or [],
        "maturity_wall": wall,
    }
    if liquidity is not None:
        ov["liquidity"] = {"total_liquidity": _cv(liquidity)}
    return ov


def test_back_to_front_regression(monkeypatch):
    # last wall unrefinanceable (CCC-band conditional) flags the earlier refi-needing
    # wall despite its own low near PD
    monkeypatch.setattr(refi, "conditional_pds", lambda *a, **k: {
        "cum": [0.01, 0.02, 0.30], "conditional": [0.01, 0.0101, 0.2857],
        "converged": True})
    wall = [{"year": 2027, "face": 500e6, "instruments": ["Term Loan"]},
            {"year": 2028, "face": 300e6, "instruments": ["8% Notes"]},
            {"year": 2031, "face": 800e6, "instruments": ["9% Notes"]}]
    out = build_refi_wall(_ov(wall, liquidity=100e6), [],
                          {"E": 1.0, "sigma_E": 1.0, "D": 1.0, "r": 0.04,
                           "as_of": "2026-07-01", "file": "ZZREFI_10y.json"})
    rows = out["rows"]
    # all need refi (liquidity 100 vs walls 500/300/800; negative EBITDA -> no sweep)
    assert all(r["refi_need"]["value"] > 0 for r in rows)
    assert rows[2]["verdict"] == "unrefinanceable"          # own CCC-band conditional
    assert rows[1]["verdict"] == "unrefinanceable"          # shows the 2028->2031 interval
    assert rows[0]["verdict"] == "unrefinanceable"          # inherited, near cond 1% only
    assert rows[0]["cond_pd"] == pytest.approx(0.0101)      # to the NEXT wall
    assert rows[0]["band"] != "CCC/C"


def test_chipco_refi_prob_and_markets_closed():
    # Chipco: bucket quote 85 over a longer pari anchor at 70 -> (85−70)/(100−70) = 50%
    sched = [{"instrument": "8% Notes due 2027", "outstanding": _cv(500e6),
              "coupon_pct": 8.0, "maturity": "2027", "secured": False},
             {"instrument": "9% Notes due 2031", "outstanding": _cv(800e6),
              "coupon_pct": 9.0, "maturity": "2031", "secured": False}]
    bonds = [{"coupon": 8.0, "maturity": "2027-06-01", "last_price": 85.0,
              "last_yield": 43.2},
             {"coupon": 9.0, "maturity": "2031-06-01", "last_price": 70.0,
              "last_yield": 12.0}]
    wall = [{"year": 2027, "face": 500e6, "instruments": ["8% Notes due 2027"]},
            {"year": 2031, "face": 800e6, "instruments": ["9% Notes due 2031"]}]
    out = build_refi_wall(_ov(wall, schedule=sched, liquidity=100e6), bonds, None)
    rows = out["rows"]
    assert rows[0]["refi_prob_pct"] == 50.0
    assert rows[0]["markets_closed"] is True                # ytm 43.2 >= 40
    assert rows[1]["markets_closed"] is False               # ytm 12 -> open
    assert rows[1]["refi_prob_pct"] is None                 # no longer-dated pari quote
    assert "no strictly-longer" in rows[1]["refi_prob_note"]
    # no hazard cache -> the note names the proxy
    assert "run Default Risk once" in out["hazard_note"]


def test_lcid_negative_ebitda_persona():
    # negative EBITDA: internal capacity $0 (noted), liquidity carries the sequencing —
    # the real LCID shape: 1,319 of liquidity covers the 204 and 1,085 walls
    # sequentially, leaving 30 against the 963 wall -> need 933
    wall = [{"year": 2026, "face": 204e6, "instruments": ["2026 Notes"]},
            {"year": 2030, "face": 1085e6, "instruments": ["2030 Notes"]},
            {"year": 2031, "face": 963e6, "instruments": ["2031 Notes"]}]
    out = build_refi_wall(_ov(wall, ebitda=-3.3e9, liquidity=1319e6), [], None)
    assert out["available"] is True
    assert any("internal repayment capacity set to $0" in n for n in out["notes"])
    assert out["rows"][0]["refi_need"]["value"] == 0.0      # liquidity covers 204
    assert out["rows"][1]["refi_need"]["value"] == 0.0      # then covers 1,085
    assert out["rows"][2]["refi_need"]["value"] == pytest.approx(933.0, abs=0.11)
    assert out["rows"][2]["repayable"]["derived"] is True


def test_stale_cache_none_liquidity_guarded():
    # AAL-shaped stale cache: no liquidity block at all -> $0 + note, no crash
    wall = [{"year": 2028, "face": 400e6, "instruments": ["Notes"]}]
    out = build_refi_wall(_ov(wall, ebitda=100e6), [], None)
    assert out["available"] is True
    assert any("no liquidity block" in n for n in out["notes"])


def test_empty_wall_and_missing_hazard_cache(tmp_path, monkeypatch):
    out = build_refi_wall(_ov([]), [], None)
    assert out["available"] is False and "no maturity wall" in out["note"]
    monkeypatch.setattr(refi, "CACHE_DIR", tmp_path)
    assert hazard_inputs("ZZREFI") is None


def test_hazard_inputs_globs_newest(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(refi, "CACHE_DIR", tmp_path)
    hzdir = tmp_path / "hazard"
    hzdir.mkdir()
    blob = {"as_of": "2026-07-01", "data": {
        "market": {"market_cap": 2.5e9, "equity_vol": 0.85},
        "features_timeline": [{"total_debt": 2.7e9}]}}
    old = hzdir / "ZZREFI_3y.json"
    old.write_text(json.dumps({**blob, "as_of": "2025-01-01"}), encoding="utf-8")
    new = hzdir / "ZZREFI_10y.json"          # glob, NOT years-keyed (the design bug)
    new.write_text(json.dumps(blob), encoding="utf-8")
    os.utime(old, (1, 1))                    # force old mtime
    hz = hazard_inputs("zzrefi")
    assert hz["file"] == "ZZREFI_10y.json" and hz["as_of"] == "2026-07-01"
    assert hz["E"] == 2.5e9 and hz["D"] == 2.7e9 and hz["r"] > 0


def test_refi_api_smoke(monkeypatch):
    from fastapi.testclient import TestClient

    import app.hazard.trace as trace
    import app.main as main
    from app.main import app
    from app.schemas import IssuerHeader, Overview

    client = TestClient(app).__enter__()
    ov = Overview(header=IssuerHeader(issuer="ATUS-shaped", ticker="ZZREFI", years=3))
    monkeypatch.setattr(main, "run_overview", lambda *a, **k: ov)
    monkeypatch.setattr(trace, "get_issuer_bonds", lambda t: {"bonds": []})
    r = client.get("/api/company/ZZREFI/capital/refi")
    assert r.status_code == 200, r.text
    assert r.json()["available"] is False    # empty wall -> graceful empty
