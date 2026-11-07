"""drop the never-used scores / watchlist / alert tables

They were created empty by 0001 and nothing ever read or wrote them. Dropped if present
so a create_all-only dev database upgrades cleanly too.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_TABLES = ("alert_log", "alerts", "watchlist_members", "watchlists", "scores")  # FK order


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for name in _TABLES:
        if name in existing:
            op.drop_table(name)


def downgrade() -> None:
    op.create_table(
        "scores",
        sa.Column("cik", sa.String(16), primary_key=True, nullable=False),
        sa.Column("score_name", sa.String(48), primary_key=True, nullable=False),
        sa.Column("asof", sa.Date(), primary_key=True, nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("components", _JSON, nullable=True),
        sa.Column("model_version", sa.String(32), nullable=True),
    )
    op.create_table(
        "watchlists",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("owner", sa.String(64), nullable=True),
        sa.Column("name", sa.String(64), nullable=False),
    )
    op.create_table(
        "watchlist_members",
        sa.Column("watchlist_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("cik", sa.String(16), primary_key=True, nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["watchlist_id"], ["watchlists.id"]),
    )
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("rule", _JSON, nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=True),
    )
    op.create_table(
        "alert_log",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("alert_id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column("fired_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["alert_id"], ["alerts.id"]),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
    )
    op.create_index("ix_alert_log_alert_id", "alert_log", ["alert_id"])
