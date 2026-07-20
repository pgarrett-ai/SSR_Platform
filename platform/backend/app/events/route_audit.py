"""python -m app.events.route_audit --date YYYY-MM-DD — Phase-6 exit-test instrument.

Routes one archived day WITHOUT inserting anything: form.idx lines for the date ->
per-CIK submissions -> registry detectors -> CSV for hand-labeling (routing precision:
target >=95% of a hand-checked day's 8-Ks routed correctly) + latency stats when
acceptanceDateTime is available (median detected-vs-accepted; the live measurement is
detected_at - accepted_at over a live window; UNVERIFIED #3 fallback: date-granularity
bound, reported as such, never faked)."""
from __future__ import annotations

import csv
import datetime as dt
import sys

from . import detectors_8k, detectors_forms  # noqa: F401 — registration
from . import edgar_feed as feed
from .poller import _idx_lines
from .registry import detectors_for, tracked_prefixes


def audit_rows(meta_pairs) -> list[dict]:
    """Pure: (FilingMeta, raw) pairs -> audit dicts with routed event types."""
    out = []
    for meta, raw in meta_pairs:
        evs = [e for det in detectors_for(meta.form) for e in det(meta, raw, None)]
        out.append({
            "accession_no": meta.accession_no, "cik": meta.cik, "form": meta.form,
            "filing_date": meta.filing_date,
            "items": ",".join(meta.items or []) or ("UNKNOWN" if meta.items_unknown else ""),
            "routed_event_types": ";".join(sorted({e.event_type for e in evs})),
            "accepted_at": meta.accepted_at or "",
        })
    return out


def main(date_s: str, out_path: str = "route_audit.csv", limit: int = 300) -> int:
    day = dt.date.fromisoformat(date_s)
    q = (day.month - 1) // 3 + 1
    from ..hazard.labels import _FORM_IDX
    text = feed.get_text(_FORM_IDX.format(y=day.year, q=q), timeout=180.0)
    prefixes = tuple(p for p in tracked_prefixes() if p != "4")
    ciks = sorted({c for c, d, _a in _idx_lines(text, prefixes, since=date_s)
                   if d == date_s})[:limit]
    rows: list[dict] = []
    for cik in ciks:
        try:
            data = feed.fresh_submissions(cik)
        except Exception as exc:
            print(f"WARN CIK {cik}: {exc}")
            continue
        pairs = [(m, r) for m, r in feed.tracked_rows(
            (data.get("filings") or {}).get("recent") or {}, cik, since=date_s)
            if m.filing_date == date_s]
        rows.extend(audit_rows(pairs))
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["accession_no"])
        w.writeheader()
        w.writerows(rows)
    routed = sum(1 for r in rows if r["routed_event_types"])
    print(f"{len(rows)} filings routed to CSV; {routed} produced events; "
          f"hand-label {out_path} and compute precision = correct/total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else
                          dt.date.today().isoformat()))
