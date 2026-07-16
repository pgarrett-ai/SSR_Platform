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


def test_screen_market_columns_and_badge(tmp_path, monkeypatch):
    """New Moyer columns ride the snapshot; the distress badge computes live against
    the drop-file (None here — no quotes for ZZTEST)."""
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    cache.save_overview("ZZTEST", 3, _overview())
    with session_scope() as s:
        row = s.get(models.Snapshot, "ZZTEST")
        row.net_market_leverage = 4.1
        row.creation_multiple_fulcrum = 3.0
        row.last_price = 0.5
    row = next(r for r in client.get("/api/screen").json() if r["ticker"] == "ZZTEST")
    assert row["net_market_leverage"] == 4.1
    assert row["creation_multiple_fulcrum"] == 3.0
    assert row["distress_badge"] is None      # no drop-file quotes for ZZTEST
    _cleanup()


def test_distress_badge_predicate(monkeypatch):
    """stock < $1 AND an unsecured quote < 60 -> badge; either leg failing -> off/None."""
    import app.hazard.trace as trace
    from app.main import _distress_badge

    quotes = {"ZZTEST": [{"coupon": 8.0, "maturity": "2030-06-15", "last_price": 55.0,
                          "as_of": "2026-07-01"}]}
    monkeypatch.setattr(trace, "get_issuer_bonds", lambda t: {
        "enabled": True, "bonds": quotes.get(t.upper()) or []})
    with session_scope() as s:
        s.add(models.DebtInstrumentRow(ticker="ZZTEST", instrument="8% Notes 2030",
                                       coupon_pct=8.0, maturity="2030", secured=False))
        s.flush()
        assert _distress_badge(s, "ZZTEST", 0.5) is True
        assert _distress_badge(s, "ZZTEST", 2.0) is False   # equity leg fails
        assert _distress_badge(s, "ZZTEST", None) is None
        for r in s.query(models.DebtInstrumentRow).filter_by(ticker="ZZTEST").all():
            s.delete(r)
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


def test_filing_notes_corpus_persisted_and_searchable():
    from types import SimpleNamespace

    from app.store import persist_filing_notes

    ft = SimpleNamespace(accession_no="acc-1", form_type="10-Q", filing_date="2026-05-05",
                         source_url="u", notes="The 2031 Notes mature on April 1, 2031.")
    try:
        with session_scope() as s:
            # same filing passed twice (10-K text == schedule text) must store ONE row
            persist_filing_notes(s, "ZZTEST", [ft, ft])
        with session_scope() as s:
            rows = s.query(models.FilingNotes).filter_by(ticker="ZZTEST").all()
            assert len(rows) == 1 and "2031 Notes" in rows[0].text
        if db.FTS_AVAILABLE:
            r = client.get("/api/search", params={"q": "2031 Notes", "ticker": "ZZTEST"})
            kinds = {h["source_kind"] for h in r.json()["hits"]}
            assert "notes" in kinds
    finally:
        with session_scope() as s:
            for r in s.query(models.FilingNotes).filter_by(ticker="ZZTEST").all():
                s.delete(r)
        _cleanup()
