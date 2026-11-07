"""Structural (non-8-K) header-level detectors: the form TYPE is the signal.

Schedule 13D/G and Form 4 filings are made about a company by third parties. Their
documents are not parsed, so an event's cik is whichever CIK's filing index surfaced it
(the subject company for the events feed)."""
from __future__ import annotations

from .registry import register
from .types import Event, FilingMeta

# (form prefix, event_type, severity, title label)
# "15" covers 15-12B/15-12G/15-15D; Form 15F (foreign private issuers) is not tracked.
# EDGAR files beneficial-ownership reports as "SCHEDULE 13D/13G" since Dec 2024; the
# "SC 13D/13G" rows keep older filings (backfill) detected.
_FORM_SPECS: tuple[tuple[str, str, int, str], ...] = (
    ("NT 10-K", "late_filing", 4, "Late annual report"),
    ("NT 10-Q", "late_filing", 3, "Late quarterly report"),
    ("25", "delisting", 4, "Exchange delisting filing"),
    ("15", "deregistration", 4, "Deregistration / reporting suspension"),
    ("4", "insider_filing", 1, "Insider transaction filed"),
    ("SC 13D", "stake_13d", 3, "Beneficial-ownership stake (active)"),
    ("SC 13G", "stake_13g", 2, "Beneficial-ownership stake (passive)"),
    ("SCHEDULE 13D", "stake_13d", 3, "Beneficial-ownership stake (active)"),
    ("SCHEDULE 13G", "stake_13g", 2, "Beneficial-ownership stake (passive)"),
)


def _make(prefix: str, event_type: str, severity: int, label: str):
    @register(prefix)
    def _detect(meta: FilingMeta, raw_header: dict, universe_row=None) -> list[Event]:
        who = getattr(universe_row, "ticker", None) or f"CIK {meta.cik.lstrip('0')}"
        return [Event(cik=meta.cik, event_type=event_type, subtype=meta.form,
                      severity=severity, confidence=1.0, occurred_at=meta.filing_date,
                      source="edgar", source_form=meta.form,
                      accession_no=meta.accession_no, source_url=meta.source_url,
                      title=f"{label} — {who} ({meta.form})", payload={})]
    return _detect


for _spec in _FORM_SPECS:
    _make(*_spec)
