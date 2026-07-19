"""PR-4 backfill: idx parsing, checkpoint/resume, detected_at=NULL discipline."""
from __future__ import annotations

import datetime as dt
import json

import app.events.backfill as backfill
import app.events.edgar_feed as feed
from app import models_events
from app.core.db import init_db, session_scope
from app.events.poller import _idx_lines


def test_idx_lines_tracked_forms_and_accession():
    idx = "\n".join([
        "Form Type   Company Name             CIK     Date Filed  File Name",
        "-" * 90,
        "8-K         ACME CORP                1234567 2024-02-26  edgar/data/1234567/0001234567-24-000009.txt",
        "8-K/A       ACME CORP                1234567 2024-03-01  edgar/data/1234567/0001234567-24-000010.txt",
        "NT 10-K     LATE CO                  222     2024-03-02  edgar/data/222/0000000222-24-000001.txt",
        "10-K        ON TIME INC              333     2024-02-27  edgar/data/333/x.txt",
        "425         MERGER COMMS             444     2024-02-27  edgar/data/444/y.txt",
        "4           INSIDER PERSON           555     2024-02-27  edgar/data/555/0000000555-24-000001.txt",
        "8-K         TOO OLD CO               666     2023-12-31  edgar/data/666/z.txt",
    ])
    rows = list(_idx_lines(idx, ("8-K", "NT 10-K", "4"), since="2024-01-01"))
    ciks = {c for c, _, _ in rows}
    assert ("0001234567", "2024-02-26", "0001234567-24-000009") in rows
    assert ("0000000222", "2024-03-02", "0000000222-24-000001") in rows
    assert "0000000555" in ciks               # Form 4 tracked when asked
    assert "0000000333" not in ciks           # 10-K untracked
    assert "0000000444" not in ciks           # 425 is NOT '4' (prefix guard)
    assert "0000000666" not in ciks           # before `since`
