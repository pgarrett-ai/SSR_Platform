"""8-K item detectors — pure, header-level (plan §3 item map). One factory, N spec
rows: item detectors differ only in item code, event naming, severity, and title, so
the table IS the detector set — PR-3 fills it; adding an item is one row + one test.
An 8-K whose submissions row carries no items list yields a single low-severity
8k_items_unknown event: unknown is never silently "no trigger" (Phase-3 decision #8,
same rule eightk.crisis_triggers applies)."""
from __future__ import annotations

from .registry import register
from .types import Event, FilingMeta

# The full plan-§3 item map. Row = the detector: (event_type, severity 1-5, title label).
# Severity rationale: 1.03/2.04/4.02 Moyer crown jewels -> 5; 4.01/3.01
# confidence-of-accounts/listing distress -> 4; 1.02/2.03/2.05/2.06/3.02 material
# credit/ops facts -> 3; 1.01/2.01/5.02 high-volume context-needed -> 2 (5.02 noisiness
# documented at eightk.py:29-31); 5.07/7.01/8.01 catch-alls -> 1.
ITEM_SPECS: dict[str, tuple[str, int, str]] = {
    "1.01": ("material_agreement", 2, "Material agreement entered"),
    "1.02": ("agreement_terminated", 3, "Material agreement terminated"),
    "1.03": ("bankruptcy", 5, "Bankruptcy or receivership"),
    "2.01": ("acquisition_disposition", 2, "Completed acquisition or disposition"),
    "2.03": ("new_debt_obligation", 3, "New direct financial obligation"),
    "2.04": ("acceleration", 5, "Triggering event accelerating a direct financial obligation"),
    "2.05": ("exit_costs", 3, "Exit or disposal costs"),
    "2.06": ("impairment", 3, "Material impairment"),
    "3.01": ("delisting_notice", 4, "Delisting notice / listing-standards deficiency"),
    "3.02": ("unregistered_equity", 3, "Unregistered sale of equity (PIPE)"),
    "4.01": ("auditor_change", 4, "Auditor change"),
    "4.02": ("non_reliance", 5, "Non-reliance on prior financials (restatement)"),
    "5.02": ("officer_change", 2, "Officer/director departure or appointment"),
    "5.07": ("vote_results", 1, "Shareholder vote results"),
    "7.01": ("reg_fd", 1, "Reg FD disclosure"),
    "8.01": ("other_events", 1, "Other events"),
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
