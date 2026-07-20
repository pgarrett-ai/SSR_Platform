"""Live EDGAR poller: EFTS item-query discovery -> fresh per-CIK submissions
resolution -> registry detectors -> idempotent store. Plus the daily form.idx
reconciliation pass.

Truth (items + accession + source_url) ALWAYS comes from the submissions doc
(fields verified by eightk.py); EFTS only tells us which CIKs to look at, via the
exact query/paging pattern labels.py already exercises. Dedupe makes the sources'
overlap free. CIKs are canonical 10-digit padded throughout."""
from __future__ import annotations

import datetime as dt
from pathlib import PurePosixPath

from sqlalchemy import select

from .. import models_events
from ..core.db import session_scope
from ..hazard.labels import _FORM_IDX                  # labels.py:58
from . import detectors_8k                             # noqa: F401 — registration side effect
from . import detectors_forms                          # noqa: F401 — registration side effect
from . import edgar_feed as feed
from . import universe
from .registry import detectors_for, tracked_prefixes
from .store import has_event, insert_events

_UNKNOWN = "8k_items_unknown"

# Form 4 is excluded from market-wide catch-up in P6: ~1,000-2,500/day (plan §5) of
# owner-CIK-attributed lines. Raw Form-4 ingest happens opportunistically whenever an
# issuer's submissions doc is resolved; Phase 8 owns real insider flow.
CATCHUP_EXCLUDE = ("4",)


def _item_params(item: str, start: str, end: str) -> dict:
    return {"q": f'"Item {item}"', "forms": "8-K", "startdt": start, "enddt": end}


def _hit_cik_date(hit: dict):
    """cik/file_date exactly as labels.py reads them (labels.py:110-114); cik padded."""
    src = hit.get("_source") or {}
    ciks = src.get("ciks") or src.get("cik") or []
    raw = str(ciks[0] if isinstance(ciks, list) else ciks)
    filed = src.get("file_date")
    if not raw.strip().lstrip("0") or not filed:
        return None, None
    return feed.pad_cik(raw), filed


def resolve_and_ingest(cik: str, since: str) -> int:
    """Fresh submissions doc -> tracked (meta, raw) pairs -> detectors -> insert.
    The recent window covers >=1yr (eightk.py:127), so a days-scale `since` never
    needs overflow pages here; backfill.py handles deep history."""
    data = feed.fresh_submissions(cik)
    pairs = feed.tracked_rows((data.get("filings") or {}).get("recent") or {},
                              cik, since=since)
    now = dt.datetime.utcnow()
    with session_scope() as session:
        universe.enrich_from_submissions(session, feed.pad_cik(cik), data)
        row = session.get(models_events.UniverseCompany, feed.pad_cik(cik))
        events = [ev for meta, raw in pairs
                  for det in detectors_for(meta.form)
                  for ev in det(meta, raw, row)]
        return insert_events(session, events, detected_at=now)


def poll_once() -> int:
    """One 5-minute cycle. Stateless by design: the window is always the last two
    calendar days; has_event() + dedupe make re-scanning free, and a CIK whose
    submissions doc lags EFTS self-heals on the next cycle."""
    today = dt.date.today()
    start, end = (today - dt.timedelta(days=1)).isoformat(), today.isoformat()
    todo: set[str] = set()
    with session_scope() as session:
        for item, (event_type, _sev, _label) in sorted(detectors_8k.ITEM_SPECS.items()):
            for hit in feed.efts_hits(_item_params(item, start, end)):
                cik, filed = _hit_cik_date(hit)
                if cik and not has_event(session, cik, filed, (event_type, _UNKNOWN)):
                    todo.add(cik)
    return sum(resolve_and_ingest(cik, since=start) for cik in sorted(todo))


def _line_form(line: str, prefix: str) -> bool:
    if not line.startswith(prefix):
        return False
    nxt = line[len(prefix):len(prefix) + 1]
    return nxt in (" ", "/", "-")


def _idx_lines(text: str, prefixes, since: str):
    """(padded_cik, date, accession) from tracked-form lines of a form.idx. Layout per
    labels.ten_k_ciks (labels.py:257-267): company names hold spaces, so fields come
    off the END. Accession = FILENAME stem — format UNVERIFIED (ledger #5); a wrong
    parse only causes extra re-resolution, never a wrong event."""
    for line in text.splitlines():
        if not any(_line_form(line, p) for p in prefixes):
            continue
        parts = line.split()
        if len(parts) < 4 or not parts[-3].isdigit():
            continue
        date = parts[-2]
        if date < since:
            continue
        yield feed.pad_cik(parts[-3]), date, PurePosixPath(parts[-1]).stem


def catchup_form_idx(window_days: int = 7) -> int:
    """Daily reconciliation: anything the EFTS poll missed in the last week surfaces
    here by accession diff against the current quarter's form.idx (URL labels.py:58).
    Also the sole live path for structural forms (NT/25/15/13D/G).

    Convergence note: the accession-diff reconciles against stored events, so a resolved
    filing that yields NO tracked event is re-resolved on later runs. With only 1.03
    seeded (this PR) that means a bounded daily re-fetch burst. Once the full detector
    table lands (next PR: all 16 8-K items + items_unknown + structural forms) the diff
    converges for all signal-bearing 8-Ks; a bounded residual of all-untracked-item
    8-Ks (notably 9.01-only /A amendments and 6.0x ABS-only filings) still re-resolves
    within the window — paced, idempotent, no data loss. Revisit with a
    processed-accessions ledger only if telemetry shows churn beyond that class
    (count 9.01-only filings specifically)."""
    today = dt.date.today()
    since = (today - dt.timedelta(days=window_days)).isoformat()
    prefixes = tuple(p for p in tracked_prefixes() if p not in CATCHUP_EXCLUDE)
    q = (today.month - 1) // 3 + 1
    text = feed.get_text(_FORM_IDX.format(y=today.year, q=q))
    prev = today - dt.timedelta(days=window_days)
    pq = (prev.month - 1) // 3 + 1
    if (prev.year, pq) != (today.year, q):     # window straddles a quarter boundary
        text += "\n" + feed.get_text(_FORM_IDX.format(y=prev.year, q=pq))
    with session_scope() as session:
        known = set(session.execute(
            select(models_events.Event.accession_no)
            .where(models_events.Event.occurred_at
                   >= dt.datetime.fromisoformat(since))).scalars())
    todo = {cik for cik, date, accession in _idx_lines(text, prefixes, since)
            if accession not in known}
    return sum(resolve_and_ingest(cik, since=since) for cik in sorted(todo))
