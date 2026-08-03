"""Dataclass adapter onto THE conflict-policy seam (models_events.insert_events).
One policy implementation lives there; this module only converts detector output
(types.Event, occurred_at as ISO date) into event-table row dicts and stamps
detected_at (now for the poller, None for backfill — plan §10)."""
from __future__ import annotations

import datetime as dt
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models_events as me
from .types import Event


def _to_row(e: Event, detected_at: Optional[dt.datetime]) -> dict:
    occurred = (dt.datetime.fromisoformat(e.occurred_at)
                if e.occurred_at else dt.datetime.utcnow())
    return {
        "cik": e.cik, "event_type": e.event_type, "subtype": e.subtype,
        "severity": e.severity, "confidence": e.confidence,
        "occurred_at": occurred, "detected_at": detected_at,
        "source": e.source, "source_form": e.source_form,
        "accession_no": e.accession_no, "source_url": e.source_url,
        "title": e.title, "payload": e.payload or {},
        "dedupe_key": e.dedupe_key,
    }


def insert_events(session: Session, events: list[Event],
                  detected_at: Optional[dt.datetime]) -> int:
    """detected_at=None is ONLY for backfill. Returns NEW rows (policy in models_events:
    dedupe idempotent; NULL->non-NULL detected_at upgrade exactly once)."""
    return me.insert_events(session, [_to_row(e, detected_at) for e in events])


DOCKET_SUBTYPES = {   # Moyer ch.12 milestones -> severity 1-5
    "petition": 5, "first_day": 3, "dip": 4, "363_sale": 4,
    "disclosure_statement": 3, "plan": 4, "confirmation": 5,
    "effective": 4, "exclusivity_extension": 2}


def docket_event(cik: str, b) -> Event:
    """Layer A (manual) docket row from an analyst entry (duck-typed body: subtype,
    occurred_at, title, docket_no, source_url). Pure -> unit-testable, mirrors
    events_from_sd_rows."""
    # cik in synthetic accession because make_dedupe_key omits cik (models_events.py:~146)
    acc = f"manual:docket:{cik}:{b.occurred_at}:{b.subtype}" + (f":{b.docket_no}" if b.docket_no else "")
    return Event(cik=cik, event_type="docket", subtype=b.subtype,
                 severity=DOCKET_SUBTYPES[b.subtype], confidence=1.0,
                 occurred_at=b.occurred_at, source="manual", source_form="docket",
                 accession_no=acc, source_url=b.source_url, title=b.title, payload={})


def has_event(session: Session, cik: str, occurred_on: str,
              event_types: Iterable[str]) -> bool:
    """Cheap pre-resolution check: any of these event types for this CIK on this date?
    occurred_at is a naive datetime column; occurred_on is an ISO date."""
    day = dt.datetime.fromisoformat(occurred_on)
    q = (select(me.Event.id)
         .where(me.Event.cik == cik,
                me.Event.occurred_at >= day,
                me.Event.occurred_at < day + dt.timedelta(days=1),
                me.Event.event_type.in_(tuple(event_types)))
         .limit(1))
    return session.execute(q).first() is not None
