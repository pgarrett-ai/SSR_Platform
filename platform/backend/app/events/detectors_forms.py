"""Structural (non-8-K) header-level detectors: the form TYPE is the signal.

Attribution honesty (ledger #6): SC 13D/G and Form 4 filings are made ABOUT a company
by third parties; whether they surface on the subject/issuer's submissions doc is
unverified. Until Phase 8 parses the XML, an event's cik is whichever CIK's doc
surfaced the filing — fine for the timeline, re-attributed properly in Phase 8."""
from __future__ import annotations

from .registry import register
from .types import Event, FilingMeta

# (form prefix, event_type, severity, title label)
# Scope note: "15" covers 15-12B/15-12G/15-15D; Form 15F (foreign-private-issuer
# deregistration) is deliberately out of P6 scope — add a "15F" row if FPI coverage matters.
_FORM_SPECS: tuple[tuple[str, str, int, str], ...] = (
    ("NT 10-K", "late_filing", 4, "Late annual report"),
    ("NT 10-Q", "late_filing", 3, "Late quarterly report"),
    ("25", "delisting", 4, "Exchange delisting filing"),
    ("15", "deregistration", 4, "Deregistration / reporting suspension"),
    ("4", "insider_filing", 1, "Insider transaction filed"),          # raw ingest only in P6
    ("SC 13D", "stake_13d", 3, "Beneficial-ownership stake (active)"),
    ("SC 13G", "stake_13g", 2, "Beneficial-ownership stake (passive)"),
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
