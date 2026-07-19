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
