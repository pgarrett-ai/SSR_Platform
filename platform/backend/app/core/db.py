"""SQLAlchemy engine/session for the single SQLite file."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

_settings = get_settings()

# check_same_thread=False so FastAPI's threadpool can share the engine; SQLite is fine for MVP.
engine = create_engine(
    _settings.database_url,
    connect_args={"check_same_thread": False},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


# FTS5 full-text search over covenant clauses / MD&A / OBS narratives. Regular
# (self-storing) virtual table through the same engine — no second sqlite3 connection.
# ponytail: stores a text copy (~MBs of MD&A); external-content FTS if DB size matters.
FTS_AVAILABLE = True

_FTS_DDL = """CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(
    text, source_kind UNINDEXED, ticker UNINDEXED, ref_id UNINDEXED)"""


def init_db() -> None:
    """Create all tables + the FTS index, and seed both from existing data when empty.
    Safe to call repeatedly."""
    global FTS_AVAILABLE
    from .. import models  # noqa: F401  (register mappers)

    models.Base.metadata.create_all(bind=engine)
    _ensure_columns()
    try:
        with engine.begin() as con:
            con.exec_driver_sql(_FTS_DDL)
    except Exception:   # this Python's sqlite3 lacks FTS5 -> search degrades to empty
        FTS_AVAILABLE = False
    _backfill()


def _ensure_columns() -> None:
    """Idempotent micro-migration: create_all never ALTERs an existing table, so add any
    mapped columns missing from the live SQLite file. Nullable ADD COLUMN only."""
    from .. import models

    with engine.begin() as con:
        for table in models.Base.metadata.tables.values():
            have = {row[1] for row in
                    con.exec_driver_sql(f"PRAGMA table_info({table.name})").fetchall()}
            if not have:   # table doesn't exist yet; create_all handled it
                continue
            for col in table.columns:
                if col.name not in have:
                    con.exec_driver_sql(
                        f"ALTER TABLE {table.name} ADD COLUMN {col.name} "
                        f"{col.type.compile(engine.dialect)}")


def _backfill() -> None:
    """One-off seeds, each guarded on its target being empty so restarts are no-ops."""
    from sqlalchemy import text as sql

    from .. import models
    from ..store import rebuild_fts

    with session_scope() as session:
        if session.query(models.Snapshot).count() == 0:
            from .cache import CACHE_DIR, load_overview
            from ..store import upsert_snapshot
            for p in sorted(CACHE_DIR.glob("*_*y.json")):
                ticker, _, yrs = p.stem.rpartition("_")
                ov = load_overview(ticker, int(yrs[:-1])) if ticker else None
                if ov is not None:
                    upsert_snapshot(session, ticker.upper(), ov)
                    # autoflush is off: without this, two cache files for one ticker
                    # (AAL_3y + AAL_10y) both INSERT and violate the ticker PK.
                    session.flush()
        if FTS_AVAILABLE and session.execute(sql("SELECT count(*) FROM search")).scalar() == 0:
            tickers = session.execute(sql(
                "SELECT ticker FROM covenants UNION SELECT ticker FROM mdna_sections "
                "UNION SELECT ticker FROM obs_items")).scalars().all()
            for t in tickers:
                rebuild_fts(session, t)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session for pipeline code."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
