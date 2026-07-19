"""Event-store ORM (SPECIAL_SITUATIONS_PLAN §4) + the idempotent insert seam.

Separate from models.py on purpose: these tables are CIK-keyed (String(16), stored
10-digit zero-padded — the only primary key at universe scale, plan §1; matches the
legacy Filing.cik/Snapshot.cik typing and the eightk.py zfill(10) convention), carry
the point-in-time discipline (occurred_at = when the world changed, detected_at = when
WE saw it, NULL for backfill — never faked), and migrate via Alembic only. models.py's
legacy tables stay on create_all + the frozen _ensure_columns micro-migration;
core/db._ensure_columns skips everything in EVENT_STORE_TABLES.

Datetimes are naive UTC by convention (pysqlite drops tz offsets on round-trip)."""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer,
    JSON, String, Text, UniqueConstraint, bindparam, func, update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from .models import Base

# JSON on SQLite, JSONB on Postgres (plan §2: "JSON columns -> JSONB").
JSON_VARIANT = JSON().with_variant(JSONB(), "postgresql")

# core/db._ensure_columns skips these: event-store DDL changes go through Alembic only.
EVENT_STORE_TABLES = frozenset({
    "universe", "events", "scores", "watchlists", "watchlist_members",
    "alerts", "alert_log",
})


class UniverseCompany(Base):
    """One EDGAR filer. Refreshed daily from company_tickers.json + submissions
    (PR-2b's job); ticker/name are display metadata — CIK is the key (plan §1)."""

    __tablename__ = "universe"

    cik: Mapped[str] = mapped_column(String(16), primary_key=True)
    ticker: Mapped[Optional[str]] = mapped_column(String(16), index=True)
    name: Mapped[Optional[str]] = mapped_column(Text)
    exchange: Mapped[Optional[str]] = mapped_column(String(16))
    sic: Mapped[Optional[str]] = mapped_column(String(8))
    market_cap: Mapped[Optional[float]] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class Event(Base):
    """One detected (or backfilled) event. detected_at NULL == backfill row —
    the Phase-12 backtest replays detected_at, so it is never faked (plan §10)."""

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_event_dedupe"),
        Index("ix_events_cik_occurred", "cik", "occurred_at"),
        Index("ix_events_type_detected", "event_type", "detected_at"),
        CheckConstraint("severity BETWEEN 1 AND 5", name="ck_event_severity"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_event_confidence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cik: Mapped[str] = mapped_column(String(16), ForeignKey("universe.cik"))
    event_type: Mapped[str] = mapped_column(String(48))
    subtype: Mapped[Optional[str]] = mapped_column(String(48))
    severity: Mapped[int] = mapped_column(Integer, default=1)        # 1-5
    confidence: Mapped[float] = mapped_column(Float, default=1.0)    # 0-1
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    detected_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(16), default="edgar")  # edgar|ratings|manual
    source_form: Mapped[Optional[str]] = mapped_column(String(16))
    accession_no: Mapped[Optional[str]] = mapped_column(String(64))
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    title: Mapped[Optional[str]] = mapped_column(Text)
    payload: Mapped[Optional[dict]] = mapped_column(JSON_VARIANT)
    dedupe_key: Mapped[str] = mapped_column(String(64))              # sha256 hex


class Score(Base):
    """Nightly batch score observation — time series by construction (plan §4)."""

    __tablename__ = "scores"

    cik: Mapped[str] = mapped_column(String(16), ForeignKey("universe.cik"),
                                     primary_key=True)
    score_name: Mapped[str] = mapped_column(String(48), primary_key=True)
    asof: Mapped[date] = mapped_column(Date, primary_key=True)
    value: Mapped[Optional[float]] = mapped_column(Float)
    components: Mapped[Optional[dict]] = mapped_column(JSON_VARIANT)
    model_version: Mapped[Optional[str]] = mapped_column(String(32))


class Watchlist(Base):
    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner: Mapped[Optional[str]] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(64))


class WatchlistMember(Base):
    __tablename__ = "watchlist_members"

    watchlist_id: Mapped[int] = mapped_column(ForeignKey("watchlists.id"),
                                              primary_key=True)
    cik: Mapped[str] = mapped_column(String(16), ForeignKey("universe.cik"),
                                     primary_key=True)
    note: Mapped[Optional[str]] = mapped_column(Text)


class Alert(Base):
    """A saved alert rule: event types x severity x score deltas x watchlist scope."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule: Mapped[dict] = mapped_column(JSON_VARIANT)
    channel: Mapped[str] = mapped_column(String(16), default="ui")   # ui|email|webhook
    created_by: Mapped[Optional[str]] = mapped_column(String(64))


class AlertLog(Base):
    """One firing of an alert. Surrogate id PK: the same rule may legitimately
    re-fire for the same event after a redelivery."""

    __tablename__ = "alert_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id"), index=True)
    event_id: Mapped[Optional[int]] = mapped_column(ForeignKey("events.id"))
    fired_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
