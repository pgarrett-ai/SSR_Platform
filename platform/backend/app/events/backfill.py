"""python -m app.events.backfill — seed the event store 2020->present.

Strategy (math in the PR description): quarterly form.idx files (URL labels.py:58)
enumerate every CIK that filed a tracked form in the window — dead filers included,
which is the whole point (today's company_tickers.json misses exactly the bankrupt
names). Per-CIK submissions docs (eightk.py pattern, overflow pages followed as at
eightk.py:134-141) then give items + accessions for everything at ~1.6 req/CIK.
EFTS chunking is rejected: 10-hit pages, a 400-hit/quarter sanity cap even in labels'
own harvest, documented 500-flakiness, and no verified accession field.

detected_at = NULL, ALWAYS (plan §10: backfilled history never masquerades as
detected). Checkpoint/resume: data/backfill.db, the labels.py panel.db pattern —
per-CIK commit, failures not marked done so they retry."""
from __future__ import annotations

import datetime as dt
import sqlite3

from ..core.config import DATA_DIR
from ..core.db import session_scope
from ..hazard.labels import _FORM_IDX                 # labels.py:58
from . import detectors_8k, detectors_forms          # noqa: F401 — registration
from . import edgar_feed as feed
from .poller import _idx_lines
from .registry import detectors_for
from .store import insert_events
from .universe import enrich_from_submissions

BACKFILL_DB = DATA_DIR / "backfill.db"
BACKFILL_FORMS = ("8-K", "NT 10-K", "NT 10-Q", "25", "15", "SC 13D", "SC 13G")


def _backfill_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(BACKFILL_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS ciks_done (
        cik TEXT PRIMARY KEY, done_at TEXT NOT NULL, n_events INTEGER NOT NULL)""")
    return con


def _quarter_texts(start: str):
    """Yield each quarterly form.idx text in [start, today] — seam for tests. Missing/
    future quarters tolerated the way harvest_pit_universe tolerates them."""
    today = dt.date.today()
    for y in range(int(start[:4]), today.year + 1):
        for q in (1, 2, 3, 4):
            if dt.date(y, 3 * q - 2, 1) > today:
                break
            try:
                yield feed.get_text(_FORM_IDX.format(y=y, q=q), timeout=180.0)
            except Exception:
                continue


def _resolve_cik_full(cik: str, since: str, prefixes) -> int:
    """Recent window + overflow pages (the eightk.py:134-141 walk, paced by paced_get),
    routed through the registry, inserted with detected_at=NULL."""
    data = feed.fresh_submissions(cik)
    filings = data.get("filings") or {}
    pairs = feed.tracked_rows(filings.get("recent") or {}, cik, since=since,
                              prefixes=prefixes)
    for f in (filings.get("files") or []):
        name = f.get("name")
        if not name:
            continue
        if f.get("filingTo") and f["filingTo"] < since:
            continue                       # page entirely older than the window
        page = feed.get_json("https://data.sec.gov/submissions/" + name)  # eightk.py:90
        pairs.extend(feed.tracked_rows(page, cik, since=since, prefixes=prefixes))
    with session_scope() as session:
        enrich_from_submissions(session, feed.pad_cik(cik), data)
        events = [ev for meta, raw in pairs
                  for det in detectors_for(meta.form)
                  for ev in det(meta, raw, None)]
        return insert_events(session, events, detected_at=None)   # NEVER faked


def backfill(start: str = "2020-01-01", include_form4: bool = False,
             limit: int | None = None) -> dict:
    prefixes = BACKFILL_FORMS + (("4",) if include_form4 else ())
    ciks: set[str] = set()
    for text in _quarter_texts(start):
        ciks |= {c for c, _d, _a in _idx_lines(text, prefixes, since=start)}
        print(f"  index pass: {len(ciks)} cumulative CIKs")
    con = _backfill_db()
    done = {row[0] for row in con.execute("SELECT cik FROM ciks_done")}
    todo = sorted(ciks - done)
    if limit:
        todo = todo[:limit]
    total = 0
    for i, cik in enumerate(todo, 1):
        try:
            n = _resolve_cik_full(cik, since=start, prefixes=prefixes)
        except Exception as exc:
            print(f"  WARN CIK {cik}: {type(exc).__name__}: {exc}")   # retried next run
            continue
        con.execute("INSERT OR REPLACE INTO ciks_done VALUES (?,?,?)",
                    (cik, dt.date.today().isoformat(), n))
        con.commit()                       # per-CIK crash checkpoint (panel.db pattern)
        total += n
        if i % 200 == 0:
            print(f"  {i}/{len(todo)} CIKs, {total} events")
    return {"ciks": len(todo), "events": total}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="Seed the event store from EDGAR history (detected_at=NULL).")
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--include-form4", action="store_true",
                    help="~2-4M raw Form 4 rows (plan §5 volume); Phase 8 rebuilds "
                         "insiders from XML — default off")
    ap.add_argument("--limit", type=int, default=None, help="max CIKs this run (smoke: 25)")
    args = ap.parse_args()
    out = backfill(args.start, include_form4=args.include_form4, limit=args.limit)
    print(f"backfill: {out['ciks']} CIKs processed, {out['events']} events inserted")
