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
