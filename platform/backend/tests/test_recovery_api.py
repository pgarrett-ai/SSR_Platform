"""Phase 1 exit test: the Project-Apex worked example reproduced through the API,
plus a scenarios save/list/delete roundtrip. No network needed."""
from fastapi.testclient import TestClient

from app.main import app

# Context-managed so the lifespan handler runs (init_db creates the scenarios table).
client = TestClient(app).__enter__()

APEX_STRUCTURE = {
    "name": "Project Apex",
    "entities": [
        {"name": "OpCo", "ev_share": 1.00, "parent": "HoldCo"},
        {"name": "HoldCo", "ev_share": 0.00, "parent": None},
    ],
    "tranches": [
        {"name": "1L Term Loan", "entity": "OpCo", "face": 500.0, "lien_rank": 1, "secured": True, "coupon": 0.085},
        {"name": "2L Notes", "entity": "OpCo", "face": 250.0, "lien_rank": 2, "secured": True, "coupon": 0.105},
        {"name": "OpCo Unsecured", "entity": "OpCo", "face": 120.0, "secured": False, "coupon": 0.075},
        {"name": "HoldCo Unsecured", "entity": "HoldCo", "face": 150.0, "secured": False, "coupon": 0.090},
    ],
    "admin_fees": 30.0,
}

APEX_SIM = {
    "base_ebitda": 120.0, "horizon_years": 1.5, "ebitda_vol": 0.28, "mean_reversion": 0.6,
    "stress_prob": 0.30, "stress_vol": 0.55, "stress_log_drift": -0.35, "base_multiple": 6.0,
    "distress_multiple": 4.5, "multiple_vol": 0.18, "corr": 0.55, "n_draws": 100_000, "seed": 7,
}


def test_project_apex_via_api():
    r = client.post("/api/company/APEX/recovery/simulate",
                    json={"structure": APEX_STRUCTURE, "sim": APEX_SIM})
    assert r.status_code == 200, r.text
    d = r.json()
    # Canonical numbers from fulcrum's README / examples.project_apex (same seed & draws).
    assert d["fulcrum"] == "2L Notes"
    assert abs(d["ev"]["median"] - 625) < 10
    assert d["total_face"] == 1020.0
    by_name = {t["tranche"]: t for t in d["tranches"]}
    assert abs(by_name["1L Term Loan"]["mean_recovery_%"] - 87.7) < 1.5
    assert abs(by_name["2L Notes"]["mean_recovery_%"] - 46.4) < 1.5
    # HoldCo paper is structurally subordinated below OpCo unsecured.
    assert by_name["HoldCo Unsecured"]["prob_zero_%"] > by_name["OpCo Unsecured"]["prob_zero_%"]
    # Chart payloads present and shaped for the UI.
    assert set(d["histograms"]) == {t["name"] for t in APEX_STRUCTURE["tranches"]}
    assert len(d["cdf"]["grid"]) == 51
    assert len(d["waterfall_at_median"]) == 4


def test_petition_date_derives_accrual():
    from app.main import _accrual_from_petition

    ov = {"debt_schedule_asof": "2026-03-31"}
    assert abs(_accrual_from_petition("2026-09-30", ov) - 183 / 365.25) < 1e-9
    assert _accrual_from_petition("2026-01-01", ov) == 0.0     # floored at 0
    assert _accrual_from_petition("2026-09-30", {}) == 0.0     # no as-of


def test_attack_scenario_rides_same_draws():
    r = client.post("/api/company/APEX/recovery/simulate", json={
        "structure": APEX_STRUCTURE, "sim": {**APEX_SIM, "n_draws": 20_000},
        "attack": "lien_avoidance"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["attack"] == "lien_avoidance"
    base = {t["tranche"]: t["mean_recovery_%"] for t in d["tranches"]}
    hit = {t["tranche"]: t["mean_recovery_%"] for t in d["attack_tranches"]}
    assert hit["1L Term Loan"] < base["1L Term Loan"]          # lien avoided
    assert hit["OpCo Unsecured"] > base["OpCo Unsecured"]      # pool gains


def test_priming_scenario_rides_same_draws():
    r = client.post("/api/company/APEX/recovery/simulate", json={
        "structure": APEX_STRUCTURE, "sim": {**APEX_SIM, "n_draws": 20_000},
        "priming": {"face": 300.0}})
    assert r.status_code == 200, r.text
    d = r.json()
    base = {t["tranche"]: t["mean_recovery_%"] for t in d["tranches"]}
    primed = {t["tranche"]: t["mean_recovery_%"] for t in d["priming_tranches"]}
    assert primed["Priming loan"] > base["1L Term Loan"]         # rank 0 pays first
    assert primed["1L Term Loan"] < base["1L Term Loan"]         # primed down
    assert primed["OpCo Unsecured"] < base["OpCo Unsecured"]     # the Moyer point
    assert d["primed_structure"]["tranches"][0]["lien_rank"] == 0
    # same EV draws: the base tranche table is unchanged by the priming overlay
    plain = client.post("/api/company/APEX/recovery/simulate", json={
        "structure": APEX_STRUCTURE, "sim": {**APEX_SIM, "n_draws": 20_000}}).json()
    assert [t["mean_recovery_%"] for t in d["tranches"]] == \
           [t["mean_recovery_%"] for t in plain["tranches"]]


def test_priming_negative_face_400():
    r = client.post("/api/company/APEX/recovery/simulate", json={
        "structure": APEX_STRUCTURE, "sim": {**APEX_SIM, "n_draws": 5_000},
        "priming": {"face": -100.0}})
    assert r.status_code == 400


def test_506_headroom_reported():
    s = {**APEX_STRUCTURE, "tranches": [
        {**APEX_STRUCTURE["tranches"][0], "collateral_value": 900.0},
        *APEX_STRUCTURE["tranches"][1:]]}
    r = client.post("/api/company/APEX/recovery/simulate",
                    json={"structure": s, "sim": {**APEX_SIM, "n_draws": 20_000}})
    assert r.status_code == 200, r.text
    hr = r.json()["headroom_506"]
    # collateral 900 vs claim 500 (accrual 0) -> 400 of postpetition-interest headroom
    assert abs(hr["1L Term Loan"] - 400.0) < 0.5


def _patch_offline(monkeypatch):
    """No cached overview / drop-file needed — the endpoint's ov + bonds legs degrade."""
    import app.hazard.trace as trace
    import app.main as main
    from app.schemas import IssuerHeader, Overview

    monkeypatch.setattr(main, "run_overview", lambda *a, **k: Overview(
        header=IssuerHeader(issuer="Apex", ticker="APEX", years=3)))
    monkeypatch.setattr(trace, "get_issuer_bonds", lambda t: {"bonds": []})


def test_exchange_endpoint_smoke(monkeypatch):
    _patch_offline(monkeypatch)
    r = client.post("/api/company/APEX/recovery/exchange", json={
        "structure": APEX_STRUCTURE, "sim": {"base_ebitda": 120.0},
        "target": "OpCo Unsecured", "ratio_pct": 50.0, "participation_pct": 90.0,
        "seniority": "priming", "equity_pct_at_full": 90.0, "min_tender_pct": 50.0})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["available"] is True and len(d["ev_grid"]) == 241
    ps = [s["participation_pct"] for s in d["scenarios"]]
    assert ps == sorted(ps) and {0.0, 90.0, 100.0} <= set(ps)
    s0 = next(s for s in d["scenarios"] if s["participation_pct"] == 0.0)
    assert s0["holdout"] == d["base_pct"]          # p=0 ≡ base on the same grid
    s90 = next(s for s in d["scenarios"] if s["participation_pct"] == 90.0)
    assert len(s90["tender"]) == 241 and s90["fails"] is False
    # Cash-free crossover regression: the leading both-zero plateau (EV under the
    # 30 admin floor) must not read as a crossover at ~0. Hand check: the stub's
    # holdout line starts after admin 30 + 1L 500 + 2L 250 + new paper p·F·ratio,
    # and catches the 50-per-100 tender plateau after (1−p)·F·ratio more —
    # 30 + 500 + 250 + 0.5·120 = 840 at any p.
    assert abs(s90["crossover_ev"] - 840.0) < 0.01
    s25 = next(s for s in d["scenarios"] if s["participation_pct"] == 25.0)
    assert s25["fails"] is True                    # below min_tender 50
    assert abs(s25["crossover_ev"] - 840.0) < 0.01               # p-invariant here
    assert d["min_tender_pct"] == 50.0
    assert d["quote_premium"] is None              # unquoted-degrading


def test_exchange_unknown_target_400(monkeypatch):
    _patch_offline(monkeypatch)
    r = client.post("/api/company/APEX/recovery/exchange", json={
        "structure": APEX_STRUCTURE, "sim": {"base_ebitda": 120.0},
        "target": "Nope", "ratio_pct": 50.0})
    assert r.status_code == 400


def test_scenarios_roundtrip():
    saved = client.post("/api/company/APEX/scenarios", json={
        "name": "Base", "sim": APEX_SIM, "structure": APEX_STRUCTURE,
        "results": {"fulcrum": "2L Notes", "ev_median": 625},
    })
    assert saved.status_code == 200
    sid = saved.json()["id"]
    listed = client.get("/api/company/APEX/scenarios").json()
    assert any(s["id"] == sid and s["name"] == "Base" for s in listed)
    assert client.delete(f"/api/scenarios/{sid}").json()["deleted"] == 1
    assert not any(s["id"] == sid for s in client.get("/api/company/APEX/scenarios").json())
