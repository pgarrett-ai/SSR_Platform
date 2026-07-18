"""Per-CIK 8-K item enumeration (Moyer ch. 8/12 event detection).

Uses the EDGAR **submissions API** (`data.sec.gov/submissions/CIK{10}.json`),
which is CIK-scoped by construction and carries each 8-K's `items` codes, filing
date, and accession in a single request — no full-text-search phrase queries, no
pagination, none of the market-wide fragility. An empty/absent items list is
reported as `items_unknown`, never silently treated as "no trigger" (Phase-3
decision #8).

Reused by F1 (petition date, `petition_filing`) and F3 (crisis triggers,
`crisis_triggers`). `_fetch_submissions` is the single network seam — monkeypatch
it in tests.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Optional

from ..core.config import get_settings

_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik10}.json"

# crisis-of-confidence 8-K items (Moyer ch. 8): restatement / auditor / management
CRISIS_ITEMS = {
    "4.01": "auditor change",
    "4.02": "non-reliance / restatement",
    "5.02": "officer/director departure",
}


def _fetch_submissions(cik) -> dict:
    """Network seam — the only outbound call; monkeypatched in tests."""
    url = _SUBMISSIONS.format(cik10=str(cik).lstrip("0").zfill(10))
    req = urllib.request.Request(url, headers={"User-Agent": get_settings().sec_user_agent})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _index_url(cik, accession: str) -> Optional[str]:
    if not accession:
        return None
    return (f"https://www.sec.gov/Archives/edgar/data/{str(cik).lstrip('0')}/"
            f"{accession.replace('-', '')}/{accession}-index.htm")


def list_8k_items(cik, since: Optional[str] = None) -> list[dict]:
    """All 8-K filings for a CIK, newest first (submissions order):
    {filing_date, accession, items: [str]|None, items_unknown: bool, source_url}.
    `since` is an ISO date (YYYY-MM-DD) lower bound on filing_date.
    ponytail: reads only submissions `filings.recent` (~most-recent 1000 filings / 1yr);
    a petition paged out of that window on a prolific filer is missed — follow
    `filings.files` overflow if that ever bites. A currently-distressed name's 1.03 is recent."""
    recent = (_fetch_submissions(cik).get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accns = recent.get("accessionNumber") or []
    items = recent.get("items") or []
    out: list[dict] = []
    for i, form in enumerate(forms):
        if form != "8-K":
            continue
        d = dates[i] if i < len(dates) else None
        if since and d and d < since:
            continue
        raw = items[i] if i < len(items) else ""
        codes = [c.strip() for c in str(raw).split(",") if c.strip()]
        accn = accns[i] if i < len(accns) else ""
        out.append({"filing_date": d, "accession": accn,
                    "items": codes or None, "items_unknown": not codes,
                    "source_url": _index_url(cik, accn)})
    return out


def petition_filing(cik) -> Optional[dict]:
    """Earliest 8-K carrying Item 1.03 (bankruptcy/receivership) → cited petition-date
    row {date, accession, source_url}; None if no 1.03 filing is found."""
    rows = [r for r in list_8k_items(cik) if r["items"] and "1.03" in r["items"]]
    if not rows:
        return None
    r = min(rows, key=lambda r: r["filing_date"] or "9999-99-99")
    return {"date": r["filing_date"], "accession": r["accession"],
            "source_url": r["source_url"]}


def crisis_triggers(cik, since: Optional[str] = None) -> list[dict]:
    """8-K filings carrying a crisis-of-confidence item (4.01/4.02/5.02). Rows with an
    unknown items list are still returned (triggers=None, items_unknown=True) so the
    caller reports "unknown", never "no trigger" (decision #8)."""
    out: list[dict] = []
    for r in list_8k_items(cik, since=since):
        if r["items_unknown"]:
            out.append({**r, "triggers": None})
            continue
        hits = {c: CRISIS_ITEMS[c] for c in r["items"] if c in CRISIS_ITEMS}
        if hits:
            out.append({**r, "triggers": hits})
    return out
