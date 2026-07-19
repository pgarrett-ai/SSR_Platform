"""PR-2a event store: schema, idempotent insert, detected_at point-in-time policy.
Datetimes are naive UTC by convention (pysqlite drops offsets; keeps SQLite/Postgres
behavior identical). CIKs are String(16), stored 10-digit zero-padded (eightk.py:47)."""
from datetime import datetime

import sqlalchemy as sa

from app import models_events as me
from app.core.db import engine, init_db, session_scope

init_db()   # idempotent; creates tables + FTS on the conftest temp SQLite

CIK = "0990000001"   # padded sentinel far above any real EDGAR CIK


def test_event_store_tables_and_composite_pk():
    insp = sa.inspect(engine)
    assert me.EVENT_STORE_TABLES <= set(insp.get_table_names())
    assert insp.get_pk_constraint("scores")["constrained_columns"] == [
        "cik", "score_name", "asof"]


def test_event_indexes_exist():
    names = {ix["name"] for ix in sa.inspect(engine).get_indexes("events")}
    assert {"ix_events_cik_occurred", "ix_events_type_detected"} <= names


def _row(subtype="4.01", detected_at=None):
    return dict(
        cik=CIK, event_type="8-K", subtype=subtype, severity=1, confidence=1.0,
        occurred_at=datetime(2026, 7, 1, 0, 0), detected_at=detected_at,
        source="edgar", source_form="8-K",
        accession_no="0000000000-26-000001", source_url=None,
        title="test event", payload={"items": [subtype]},
        dedupe_key=me.make_dedupe_key("0000000000-26-000001", "8-K", subtype))


def _seed_universe():
    with session_scope() as s:
        s.merge(me.UniverseCompany(cik=CIK, ticker="ZZEVT", name="ZZ Event Corp"))


def _cleanup():
    with session_scope() as s:
        s.execute(sa.delete(me.Event).where(me.Event.cik == CIK))
        s.execute(sa.delete(me.UniverseCompany).where(me.UniverseCompany.cik == CIK))


def test_dedupe_key_covers_type_and_subtype():
    k1 = me.make_dedupe_key("acc-1", "8-K", "4.01")
    assert k1 == me.make_dedupe_key("acc-1", "8-K", "4.01")    # deterministic
    assert k1 != me.make_dedupe_key("acc-1", "8-K", "5.02")    # same filing, 2nd item
    assert len(k1) == 64                                       # sha256 hex fits String(64)


def test_insert_events_is_idempotent():
    _seed_universe()
    try:
        with session_scope() as s:
            assert me.insert_events(s, [_row("4.01"), _row("5.02")]) == 2
        with session_scope() as s:   # re-poll: same filing again
            assert me.insert_events(s, [_row("4.01"), _row("5.02")]) == 0
        with session_scope() as s:
            assert s.query(me.Event).filter_by(cik=CIK).count() == 2
    finally:
        _cleanup()


def test_backfill_row_upgraded_by_live_detection():
    _seed_universe()
    try:
        with session_scope() as s:               # backfill first: detected_at NULL
            assert me.insert_events(s, [_row()]) == 1
        live = datetime(2026, 7, 2, 9, 30)
        with session_scope() as s:               # the daemon later sees the same filing
            assert me.insert_events(s, [_row(detected_at=live)]) == 0   # no new row...
        with session_scope() as s:
            row = s.query(me.Event).filter_by(cik=CIK).one()
            assert row.detected_at == live       # ...but the NULL stamp upgraded
    finally:
        _cleanup()


def test_live_detected_at_is_never_overwritten():
    _seed_universe()
    try:
        first = datetime(2026, 7, 2, 9, 30)
        with session_scope() as s:
            me.insert_events(s, [_row(detected_at=first)])
        with session_scope() as s:               # later re-poll AND a backfill re-run
            me.insert_events(s, [_row(detected_at=datetime(2026, 7, 3, 9, 30))])
            me.insert_events(s, [_row(detected_at=None)])
        with session_scope() as s:
            row = s.query(me.Event).filter_by(cik=CIK).one()
            assert row.detected_at == first      # earliest live stamp is the truth
    finally:
        _cleanup()
