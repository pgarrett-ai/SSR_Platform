"""Asset-based liquidation waterfall + the C4 regression (negative EBITDA -> 200)."""
import pytest
from fastapi.testclient import TestClient

from app.fulcrum.liquidation import FIRE_SALE, ORDERLY, assets_from_snapshot, liquidate
from app.fulcrum.structure import CapitalStructure, Entity, Tranche
from app.main import app

client = TestClient(app).__enter__()

ASSETS = {"cash": 700.0, "accounts_receivable": 100.0, "inventory": 400.0,
          "ppe": 2000.0, "intangibles": 100.0, "other": 700.0}


def _structure():
    return CapitalStructure(
        name="L", entities=[Entity("OpCo", 1.0)],
        tranches=[Tranche("1L", "OpCo", face=500.0, lien_rank=1, secured=True),
                  Tranche("Unsec", "OpCo", face=3000.0)])


def test_proceeds_math_exact():
    out = liquidate(ASSETS, _structure())
    s = out["scenario"]
    # orderly: 700 + 75 + 200 + 800 + 10 + 175 = 1960 gross; ×0.93 = 1822.8 net
    assert s["gross_proceeds"] == pytest.approx(1960.0)
    assert s["net_proceeds"] == pytest.approx(1960.0 * 0.93)
    by = {r["tranche"]: r for r in s["tranches"]}
    assert by["1L"]["recovery"] == pytest.approx(500.0)
    assert by["Unsec"]["recovery"] == pytest.approx(1960.0 * 0.93 - 500.0)  # impaired
    assert s["fulcrum"] == "Unsec"


def test_ch7_below_ch11():
    out = liquidate(ASSETS, _structure())
    ch11 = out["ch11_vs_ch7"]["ch11_orderly"]["net_proceeds"]
    ch7 = out["ch11_vs_ch7"]["ch7_fire_sale"]["net_proceeds"]
    assert ch7 < ch11
    # fire-sale halves non-cash orderly rates (cash stays 1.0)
    assert FIRE_SALE["cash"] == ORDERLY["cash"] == 1.0
    assert all(FIRE_SALE[k] <= ORDERLY[k] for k in ORDERLY)


def test_assets_from_snapshot_units():
    snap = {"cash": {"value": 700e6}, "ppe": {"value": 2e9}, "inventory": {"value": None}}
    assets = assets_from_snapshot(snap)
    assert assets == {"cash": 700.0, "ppe": 2000.0}
    assert assets_from_snapshot(None) is None
    assert assets_from_snapshot({"cash": {"value": None}}) is None


def test_negative_ebitda_returns_liquidation_mode_not_400():
    """C4 regression: the Overview's quick simulate on a negative-EBITDA name must not 400."""
    r = client.post("/api/company/NOSUCHTICKER/recovery/simulate", json={
        "structure": {"name": "L",
                      "entities": [{"name": "OpCo", "ev_share": 1.0, "parent": None}],
                      "tranches": [{"name": "Notes", "entity": "OpCo", "face": 100.0}]},
        "sim": {"base_ebitda": -5.0},
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["mode"] == "liquidation"
    assert "EBITDA" in d["note"]


def test_liquidation_endpoint_with_asset_override():
    r = client.post("/api/company/NOSUCHTICKER/recovery/liquidation", json={
        "structure": {"name": "L",
                      "entities": [{"name": "OpCo", "ev_share": 1.0, "parent": None}],
                      "tranches": [{"name": "1L", "entity": "OpCo", "face": 500.0,
                                    "lien_rank": 1, "secured": True},
                                   {"name": "Unsec", "entity": "OpCo", "face": 1000.0}]},
        "assets": ASSETS,
        "rates": {"cash": 1.0, "accounts_receivable": 0.5, "inventory": 0.25,
                  "ppe": 0.3, "intangibles": 0.0, "other": 0.1},
        "admin_pct": 0.10,
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["available"] and d["mode"] == "liquidation"
    # 700 + 50 + 100 + 600 + 0 + 70 = 1520 gross, ×0.9 net
    assert d["scenario"]["gross_proceeds"] == pytest.approx(1520.0)
    assert d["scenario"]["net_proceeds"] == pytest.approx(1368.0)
    assert "ch11_vs_ch7" in d
