"""EDGAR feed seams for the event engine. Every outbound byte goes through PR-1's
paced_get. Endpoints are the ones this codebase already exercises: the EFTS
search-index labels.py pages (labels.py:57,67-71) and the per-CIK submissions doc
eightk.py parses (eightk.py:24,100-120). tracked_rows generalizes
eightk._rows_from_arrays from 8-K-only to every registered form prefix."""
from __future__ import annotations

import json
import urllib.parse
from typing import Optional

from ..capstack.eightk import _SUBMISSIONS, _index_url
from ..edgar.http import paced_get
from .registry import form_matches
from .types import FilingMeta

_EFTS = "https://efts.sec.gov/LATEST/search-index?"          # labels.py:57


def get_bytes(url: str, timeout: float = 30.0) -> bytes:
    return paced_get(url, timeout=timeout)


def get_json(url: str, timeout: float = 30.0) -> dict:
    return json.loads(get_bytes(url, timeout=timeout).decode("utf-8"))


def get_text(url: str, timeout: float = 120.0) -> str:
    return get_bytes(url, timeout=timeout).decode("latin-1")  # form.idx charset


def pad_cik(cik) -> str:
    """Canonical stored form: 10-digit zero-padded (eightk.py:47 convention)."""
    return str(cik).strip().lstrip("0").zfill(10)


def fresh_submissions(cik) -> dict:
    """Latest submissions doc, UNCACHED — the poller must see a filing made minutes ago;
    eightk's 1h _cached TTL is for UI paths and would eat the latency budget."""
    return get_json(_SUBMISSIONS.format(cik10=pad_cik(cik)))


def efts_hits(params: dict, max_pages: int = 40) -> list[dict]:
    """Page one EFTS query exactly the way labels.py does: 10 hits/page, from+=10,
    stop at hits.total.value (labels.py:105-121). Pacing/backoff live in paced_get."""
    out: list[dict] = []
    frm = 0
    while frm < max_pages * 10:
        page = get_json(_EFTS + urllib.parse.urlencode({**params, "from": frm}))
        hits = (page.get("hits") or {}).get("hits") or []
        out.extend(hits)
        total = ((page.get("hits") or {}).get("total") or {}).get("value", 0)
        frm += 10
        if frm >= total or not hits:
            break
    return out


def tracked_rows(arrays: dict, cik, since: Optional[str] = None,
                 prefixes: Optional[tuple[str, ...]] = None) -> list[tuple[FilingMeta, dict]]:
    """(FilingMeta, raw_header) for every tracked-form filing in a submissions arrays
    dict — same parallel-array fields eightk._rows_from_arrays reads (eightk.py:103-106),
    plus acceptanceDateTime when present (UNVERIFIED #3 — None-tolerant)."""
    if prefixes is None:
        from .registry import tracked_prefixes
        prefixes = tracked_prefixes()
    forms = arrays.get("form") or []
    dates = arrays.get("filingDate") or []
    accns = arrays.get("accessionNumber") or []
    items = arrays.get("items") or []
    accepted = arrays.get("acceptanceDateTime") or []
    cik_s = pad_cik(cik)
    out: list[tuple[FilingMeta, dict]] = []
    for i, form in enumerate(forms):
        if not any(form_matches(form, p) for p in prefixes):
            continue
        d = dates[i] if i < len(dates) else None
        if since and d and d < since:
            continue
        accn = accns[i] if i < len(accns) else ""
        if not accn:
            continue
        raw_items = items[i] if i < len(items) else ""
        codes = [c.strip() for c in str(raw_items).split(",") if c.strip()]
        acc_at = (accepted[i] if i < len(accepted) else "") or None
        is_8k = form_matches(form, "8-K")
        meta = FilingMeta(cik=cik_s, form=form, filing_date=d, accession_no=accn,
                          source_url=_index_url(cik, accn),
                          items=(codes or None) if is_8k else None,
                          items_unknown=is_8k and not codes,
                          accepted_at=acc_at)
        out.append((meta, {"form": form, "filingDate": d,
                           "accessionNumber": accn, "items": raw_items}))
    return out
