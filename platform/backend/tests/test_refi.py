"""Refi-wall sequencing: sequential funding, back-to-front verdicts, refi-prob anchoring,
markets-closed threshold, degradation, bank-paper identification."""
import datetime as dt

import pytest

from app.capstack.refi import _is_bank, build_refi_wall, sequence_walls


def _cv(v):
    return {"value": v, "derived": True, "formula": "test"}


def test_bank_id_name_fallback_and_type():
    # AAL tags no facility_type — the name regex is load-bearing
    assert _is_bank({"instrument": "2014 Revolving Facility"}) is True
    assert _is_bank({"instrument": "2021 AAdvantage Term Loan Facility"}) is True
    assert _is_bank({"instrument": "ABL Credit Facility"}) is True
    assert _is_bank({"instrument": "DDTL Credit Facility"}) is True
    assert _is_bank({"instrument": "5.75% Senior Notes"}) is False
    assert _is_bank({"instrument": "Special Facility Revenue Bonds"}) is False
    # tagged facility_type wins over the name
    assert _is_bank({"instrument": "X", "facility_type": "revolver"}) is True
    assert _is_bank({"instrument": "X", "facility_type": "commercial paper"}) is False
    assert _is_bank({"instrument": "X", "facility_type": "notes"}) is False


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


def test_back_to_front_markets_closed_poisons_earlier_walls():
    # the last wall needs refi and its paper yields 45% (market closed) -> it is
    # unrefinanceable, and so is every earlier refinancing that must survive it
    sched = [{"instrument": "Term Loan", "maturity": "2027", "secured": True},
             {"instrument": "8% Notes", "maturity": "2028", "secured": False},
             {"instrument": "9% Notes", "maturity": "2031", "secured": False,
              "coupon_pct": 9.0}]
    bonds = [{"coupon": 9.0, "maturity": "2031-06-01", "last_price": 40.0,
              "last_yield": 45.0}]
    wall = [{"year": 2027, "face": 500e6, "instruments": ["Term Loan"]},
            {"year": 2028, "face": 300e6, "instruments": ["8% Notes"]},
            {"year": 2031, "face": 800e6, "instruments": ["9% Notes"]}]
    rows = build_refi_wall(_ov(wall, schedule=sched, liquidity=100e6), bonds)["rows"]
    assert all(r["refi_need"]["value"] > 0 for r in rows)
    assert [r["verdict"] for r in rows] == ["unrefinanceable"] * 3
    assert "cond_pd" not in rows[0] and "band" not in rows[0]


def test_unquoted_walls_needing_refi_are_not_called_unrefinanceable():
    # no marks -> market access is unknown; a wall that needs refi says so, nothing more
    wall = [{"year": 2027, "face": 500e6, "instruments": ["Term Loan"]},
            {"year": 2031, "face": 800e6, "instruments": ["9% Notes"]}]
    rows = build_refi_wall(_ov(wall, liquidity=100e6), [])["rows"]
    assert [r["verdict"] for r in rows] == ["refi_needed", "refi_needed"]


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
    out = build_refi_wall(_ov(wall, schedule=sched, liquidity=100e6), bonds)
    rows = out["rows"]
    assert rows[0]["refi_prob_pct"] == 50.0
    assert rows[0]["markets_closed"] is True                # ytm 43.2 >= 40
    assert rows[1]["markets_closed"] is False               # ytm 12 -> open
    assert rows[1]["refi_prob_pct"] is None                 # no longer-dated pari quote
    assert "no strictly-longer" in rows[1]["refi_prob_note"]


def test_lcid_negative_ebitda_persona():
    # negative EBITDA: internal capacity $0 (noted), liquidity carries the sequencing —
    # the real LCID shape: 1,319 of liquidity covers the 204 and 1,085 walls
    # sequentially, leaving 30 against the 963 wall -> need 933
    wall = [{"year": 2026, "face": 204e6, "instruments": ["2026 Notes"]},
            {"year": 2030, "face": 1085e6, "instruments": ["2030 Notes"]},
            {"year": 2031, "face": 963e6, "instruments": ["2031 Notes"]}]
    out = build_refi_wall(_ov(wall, ebitda=-3.3e9, liquidity=1319e6), [])
    assert out["available"] is True
    assert any("internal repayment capacity set to $0" in n for n in out["notes"])
    assert out["rows"][0]["refi_need"]["value"] == 0.0      # liquidity covers 204
    assert out["rows"][1]["refi_need"]["value"] == 0.0      # then covers 1,085
    assert out["rows"][2]["refi_need"]["value"] == pytest.approx(933.0, abs=0.11)
    assert out["rows"][2]["repayable"]["derived"] is True


def test_stale_cache_none_liquidity_guarded():
    # AAL-shaped stale cache: no liquidity block at all -> $0 + note, no crash
    wall = [{"year": 2028, "face": 400e6, "instruments": ["Notes"]}]
    out = build_refi_wall(_ov(wall, ebitda=100e6), [])
    assert out["available"] is True
    assert any("no liquidity block" in n for n in out["notes"])


def test_empty_wall():
    out = build_refi_wall(_ov([]), [])
    assert out["available"] is False and "no maturity wall" in out["note"]


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
