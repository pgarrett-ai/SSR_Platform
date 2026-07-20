"""Event-framework primitives (plan §4/§5): the detector contract's input and output.
Pure data — no I/O, no ORM. Storage mapping lives in events/store.py; detected_at is
deliberately NOT a field here: the store stamps it at insert time (now for the poller,
NULL for backfill), so a detector can never fake point-in-time provenance.
CIKs are the canonical 10-digit zero-padded string everywhere (Interface Contract)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..models_events import make_dedupe_key


@dataclass(frozen=True)
class FilingMeta:
    """Header-level facts about one filing, parsed from the submissions parallel arrays
    (field names verified by capstack/eightk._rows_from_arrays)."""
    cik: str                            # 10-digit zero-padded
    form: str                           # "8-K", "NT 10-K", "SC 13D/A", ...
    filing_date: Optional[str]          # ISO date (EDGAR filingDate)
    accession_no: str
    source_url: Optional[str] = None    # filing index page (eightk._index_url)
    items: Optional[list[str]] = None   # 8-K item codes when reported
    items_unknown: bool = False         # 8-K with an empty items list (decision #8)
    accepted_at: Optional[str] = None   # submissions acceptanceDateTime (UNVERIFIED #3;
                                        # None-tolerant everywhere — latency gauge only)


@dataclass(frozen=True)
class Event:
    """One detected event, aligned 1:1 with the events table (plan §4)."""
    cik: str                            # 10-digit zero-padded
    event_type: str
    subtype: Optional[str]
    severity: int                       # 1-5
    confidence: float                   # 0-1; 1.0 = EDGAR header metadata, not inference
    occurred_at: Optional[str]          # ISO date the world changed (filing date in v1)
    source: str                         # "edgar" | "ratings"
    source_form: str
    accession_no: str                   # synthetic "ratings:..." key for non-filing sources
    source_url: Optional[str]
    title: str
    payload: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 1 <= int(self.severity) <= 5:
            raise ValueError(f"severity {self.severity} outside 1-5")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError(f"confidence {self.confidence} outside 0-1")

    @property
    def dedupe_key(self) -> str:
        return make_dedupe_key(self.accession_no, self.event_type, self.subtype)
