"""8-K item detectors — pure, header-level (plan §3 item map). One factory, N spec
rows: item detectors differ only in item code, event naming, severity, and title, so
the table IS the detector set — PR-3 fills it; adding an item is one row + one test.
An 8-K whose submissions row carries no items list yields a single low-severity
8k_items_unknown event: unknown is never silently "no trigger" (Phase-3 decision #8,
same rule eightk.crisis_triggers applies)."""
from __future__ import annotations

from .registry import register
from .types import Event, FilingMeta

# item -> (event_type, severity, title label). PR-2b seeds 1.03; PR-3 completes.
ITEM_SPECS: dict[str, tuple[str, int, str]] = {
    "1.03": ("bankruptcy", 5, "Bankruptcy or receivership"),
}


def _who(meta: FilingMeta, universe_row) -> str:
    t = getattr(universe_row, "ticker", None)
    return t or f"CIK {meta.cik.lstrip('0')}"


@register("8-K")
def detect_8k_items(meta: FilingMeta, raw_header: dict, universe_row=None) -> list[Event]:
    if meta.items_unknown:
        return [Event(cik=meta.cik, event_type="8k_items_unknown", subtype=None,
                      severity=1, confidence=0.5, occurred_at=meta.filing_date,
                      source="edgar", source_form=meta.form,
                      accession_no=meta.accession_no, source_url=meta.source_url,
                      title=f"8-K with unreported item list — {_who(meta, universe_row)}",
                      payload={"raw_items": str((raw_header or {}).get("items") or "")})]
    out: list[Event] = []
    for item in meta.items or []:
        spec = ITEM_SPECS.get(item)
        if spec is None:
            continue        # untracked items (2.02 earnings, 9.01 exhibits, ...) skip on purpose
        event_type, severity, label = spec
        out.append(Event(cik=meta.cik, event_type=event_type, subtype=item,
                         severity=severity, confidence=1.0,   # item code IS EDGAR metadata
                         occurred_at=meta.filing_date, source="edgar",
                         source_form=meta.form, accession_no=meta.accession_no,
                         source_url=meta.source_url,
                         title=f"{label} — {_who(meta, universe_row)} (8-K Item {item})",
                         payload={"items": meta.items}))
    return out
