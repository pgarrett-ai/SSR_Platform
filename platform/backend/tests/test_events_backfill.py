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


def test_backfill_checkpoints_resumes_and_never_stamps_detected_at(tmp_path, monkeypatch):
    init_db()
    monkeypatch.setattr(backfill, "BACKFILL_DB", tmp_path / "backfill.db")
    idx = ("8-K  A CO  111 2024-02-26  edgar/data/111/0000000111-24-000001.txt\n"
           "8-K  B CO  222 2024-03-01  edgar/data/222/0000000222-24-000001.txt")
    monkeypatch.setattr(backfill, "_quarter_texts", lambda start: [idx])
    calls = []

    def fake_subs(cik):
        calls.append(cik)
        if cik == "0000000222":
            raise ConnectionError("edgar hiccup")
        return {"filings": {"recent": {
            "form": ["8-K"], "filingDate": ["2024-02-26"],
            "accessionNumber": ["0000000111-24-000001"], "items": ["2.04"]}}}

    monkeypatch.setattr(feed, "fresh_submissions", fake_subs)

    out1 = backfill.backfill(start="2024-01-01")
    assert calls == ["0000000111", "0000000222"] and out1["events"] == 1

    out2 = backfill.backfill(start="2024-01-01")
    assert calls == ["0000000111", "0000000222", "0000000222"]  # only the failure retried
    assert out2["events"] == 0

    with session_scope() as s:
        row = s.query(models_events.Event).filter_by(cik="0000000111").one()
        assert row.event_type == "acceleration"
        assert row.detected_at is None        # backfill NEVER fakes detection (plan §10)
