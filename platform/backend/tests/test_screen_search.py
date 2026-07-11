"""Storage build-out: snapshots screening index + FTS5 lexical search."""
from fastapi.testclient import TestClient

from app import models
from app.core import cache, db
from app.core.db import init_db, session_scope
from app.main import app
from app.schemas import (CitedValue, EconomicDebtBridge, ForensicFlag,
                         IssuerHeader, Overview)
from app.store import rebuild_fts, update_snapshot_risk

client = TestClient(app).__enter__()   # lifespan runs init_db (tables + FTS)


def _overview(lev=6.5):
    return Overview(
        header=IssuerHeader(issuer="ZZ Test Corp", ticker="ZZTEST", cik="99",
                            years=3, last_updated="2026-07-07T00:00:00+00:00"),
        economic_debt_bridge=EconomicDebtBridge(
            reported_leverage=CitedValue(value=5.0),
            economic_leverage=CitedValue(value=lev),
            net_economic_debt=CitedValue(value=1.2e9),
        ),
        forensic_flags=[ForensicFlag(flag_type="x", severity="warn", narrative="n")],
    )


def _cleanup():
    with session_scope() as s:
        row = s.get(models.Snapshot, "ZZTEST")
        if row is not None:
            s.delete(row)
        for m in (models.Covenant, models.MdnaSection, models.ObsItem):
            for r in s.query(m).filter_by(ticker="ZZTEST").all():
                s.delete(r)
        if db.FTS_AVAILABLE:
            from sqlalchemy import text
            s.execute(text("DELETE FROM search WHERE ticker = 'ZZTEST'"))


def test_fts5_available():
    """Hard check: the deployment Python's sqlite3 must ship FTS5."""
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
    con.execute("INSERT INTO t VALUES ('springing lien covenant')")
    assert con.execute("SELECT count(*) FROM t WHERE t MATCH 'springing'").fetchone()[0] == 1
    assert db.FTS_AVAILABLE


def test_snapshot_upsert_and_screen(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    cache.save_overview("ZZTEST", 3, _overview(lev=6.5))
    cache.save_overview("ZZTEST", 3, _overview(lev=7.1))   # latest wins
    rows = [r for r in client.get("/api/screen").json() if r["ticker"] == "ZZTEST"]
    assert len(rows) == 1
    assert rows[0]["economic_leverage"] == 7.1
    assert rows[0]["flag_count"] == 1
    _cleanup()


def test_risk_update_survives_resave(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    cache.save_overview("ZZTEST", 3, _overview())
    with session_scope() as s:
        update_snapshot_risk(s, "ZZTEST", {
            "executive_summary": {"overall_risk": 42.5},
            "scores": {"Trained hazard": {"value": 0.0014, "implied_rating": "BBB"}}})
    cache.save_overview("ZZTEST", 3, _overview(lev=8.0))   # capstack re-save
    row = next(r for r in client.get("/api/screen").json() if r["ticker"] == "ZZTEST")
    assert row["overall_risk"] == 42.5 and row["implied_rating"] == "BBB"
    assert row["economic_leverage"] == 8.0                 # merge kept both sides
    _cleanup()


def test_fts_rebuild_and_search():
    with session_scope() as s:
        s.add(models.Covenant(ticker="ZZTEST",
                              clause_text="springing maturity applies if the notes..."))
        s.add(models.MdnaSection(ticker="ZZTEST", section_name="MD&A",
                                 text="liquidity remains adequate under the revolver"))
        rebuild_fts(s, "ZZTEST")
        rebuild_fts(s, "ZZTEST")   # idempotent — no duplicate hits
    hits = client.get("/api/search", params={"q": "springing"}).json()["hits"]
    mine = [h for h in hits if h["ticker"] == "ZZTEST"]
    assert len(mine) == 1
    assert "<mark>springing</mark>" in mine[0]["snippet"]
    assert mine[0]["source_kind"] == "covenant"
    _cleanup()


def test_search_query_is_escaped():
    r = client.get("/api/search", params={"q": '"unbalanced (NEAR'})
    assert r.status_code == 200 and isinstance(r.json()["hits"], list)


def test_backfill_seeds_from_json(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    (tmp_path / "ZZTEST_3y.json").write_text(_overview().model_dump_json(),
                                             encoding="utf-8")
    with session_scope() as s:                     # empty the index so the guard fires
        for r in s.query(models.Snapshot).all():
            s.delete(r)
    init_db()
    row = next(r for r in client.get("/api/screen").json() if r["ticker"] == "ZZTEST")
    assert row["economic_leverage"] == 6.5
    _cleanup()
