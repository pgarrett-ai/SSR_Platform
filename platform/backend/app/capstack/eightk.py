"""Per-CIK 8-K item enumeration (event detection; Moyer, Distressed Debt Analysis, ch. 8
and 12).

Uses the EDGAR **submissions API** (`data.sec.gov/submissions/CIK{10}.json`),
which is CIK-scoped by construction and carries each 8-K's `items` codes, filing
date, and accession in a single request — no full-text-search phrase queries, no
pagination, none of the market-wide fragility. An empty/absent items list is
reported as `items_unknown`, never silently treated as "no trigger".

Feeds the petition date (`petition_filing`) and the crisis triggers
(`crisis_triggers`). `_http_get_submissions` is the raw network seam; `_fetch_submissions`
wraps it with a 1h per-CIK disk cache + retry. Tests monkeypatch either. All bytes leave
through the one paced EDGAR transport (`edgar.http.paced_get`).

ponytail: the petition (per overview build) and the crisis screen (per Risk page mount)
enumerate 8-K items live (1h cache); point them at the event store (which already carries
1.03/4.01/4.02 rows) once the worker + backfill have populated it, keeping this module as
the cold-start fallback.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
import urllib.error
from typing import Optional

from ..core.config import CACHE_DIR
from ..edgar.http import paced_get
from ..schemas import Citation

_today = dt.date.today   # clock seam: tests pin the date a case counts as resolved from
_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik10}.json"
_SUBMISSIONS_CACHE_DIR = CACHE_DIR / "eightk"   # per-CIK cache; module global so tests can redirect it
_SUBMISSIONS_TTL_S = 3600   # 1h — collapses page-mount refetch storms, caps petition-detection lag at ~1h

# crisis-of-confidence 8-K items (Moyer, Distressed Debt Analysis, ch. 8): restatement /
# auditor / management.
# 4.01/4.02 are the accounting-confidence signals; 5.02 also covers routine appointments
# (Item 5.02 = Departure/Election/Appointment), so it is noisier — labeled accurately and
# treated as secondary. A body-keyword read to isolate genuine departures is the upgrade path.
CRISIS_ITEMS = {
    "4.01": "auditor change",
    "4.02": "non-reliance / restatement",
    "5.02": "officer/director change",
}


def _http_get_json(url: str) -> dict:
    """Raw network seam — monkeypatched in tests; bytes leave via the paced transport."""
    return json.loads(paced_get(url).decode("utf-8"))


def _http_get_submissions(cik) -> dict:
    return _http_get_json(_SUBMISSIONS.format(cik10=str(cik).lstrip("0").zfill(10)))


def _cached(key: str, fetcher) -> dict:
    """Disk cache (1h TTL) + light retry around a JSON fetch, keyed by `key`. Keeps repeated
    /recovery/case & /recovery/crisis calls from re-hitting EDGAR (SEC's 10 req/s fair-access
    limit → IP ban); the 1h TTL still surfaces a same-day petition/restatement 8-K within the hour."""
    p = _SUBMISSIONS_CACHE_DIR / f"{key}.json"
    now = time.time()
    if p.exists():
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
            if now - float(blob.get("fetched_at") or 0) < _SUBMISSIONS_TTL_S:
                return blob["data"]
        except Exception:
            pass   # corrupt/stale cache -> refetch
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            data = fetcher()
            break
        except Exception as exc:   # transient 5xx/timeout — short backoff, then degrade
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (429, 403):
                raise   # paced_get already spent the sanctioned 429/403 backoff; don't re-amplify it
            last_exc = exc
            if attempt < 2:        # don't sleep after the final attempt
                time.sleep(0.5 * (attempt + 1))
    else:
        raise last_exc if last_exc is not None else RuntimeError("submissions fetch failed")
    try:
        _SUBMISSIONS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"fetched_at": now, "data": data}), encoding="utf-8")
    except Exception:
        pass   # cache write is best-effort
    return data


def _fetch_submissions(cik) -> dict:
    """The primary submissions doc for a CIK (cached). Tests monkeypatch this whole function."""
    return _cached(str(cik).lstrip("0").zfill(10), lambda: _http_get_submissions(cik))


def _fetch_submission_file(name: str) -> dict:
    """An overflow submissions page (older filings beyond the recent window), cached."""
    key = re.sub(r"[^A-Za-z0-9._-]", "_", name)   # EDGAR-supplied name; allowlist the cache key
    return _cached(key, lambda: _http_get_json("https://data.sec.gov/submissions/" + name))


def _index_url(cik, accession: str) -> Optional[str]:
    if not accession:
        return None
    return (f"https://www.sec.gov/Archives/edgar/data/{str(cik).lstrip('0')}/"
            f"{accession.replace('-', '')}/{accession}-index.htm")


def _rows_from_arrays(arrays: dict, cik, since: Optional[str]) -> list[dict]:
    """Extract 8-K rows from a submissions arrays dict (either `filings.recent` or an
    overflow page, which carries the same parallel arrays at its top level)."""
    forms = arrays.get("form") or []
    dates = arrays.get("filingDate") or []
    accns = arrays.get("accessionNumber") or []
    items = arrays.get("items") or []
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


def list_8k_items(cik, since: Optional[str] = None) -> list[dict]:
    """All 8-K filings for a CIK: {filing_date, accession, items: [str]|None, items_unknown,
    source_url}. Reads `filings.recent` AND follows the `filings.files` overflow pages (older
    filings beyond the ~1000/1yr recent window), so an old petition/restatement on a prolific
    filer is not missed. `since` (ISO date) bounds filing_date and skips overflow pages entirely
    older than it. Every page is 1h-cached. A persistent overflow-page fetch failure PROPAGATES
    (after _cached's retries) so the caller surfaces it as petition_error/trigger_error
    ("unknown") rather than a silent false "no trigger"."""
    data = _fetch_submissions(cik)
    filings = data.get("filings") or {}
    out = _rows_from_arrays(filings.get("recent") or {}, cik, since)
    for f in (filings.get("files") or []):
        name = f.get("name")
        if not name:
            continue
        if since and f.get("filingTo") and f["filingTo"] < since:
            continue   # this overflow page is entirely older than the window of interest
        out.extend(_rows_from_arrays(_fetch_submission_file(name), cik, since))
    return out


_CASE_GAP_DAYS = 2 * 365        # 1.03 filings closer together than this are one case
_CASE_LOOKBACK_DAYS = 3 * 365   # a case whose last filing is older has been resolved


def petition_filing(cik) -> Optional[dict]:
    """The issuer's latest Chapter 11 case from its 8-K Item 1.03 (bankruptcy/receivership)
    filings → cited petition-date row {date, accession, source_url, plan_confirmed}; None
    when there is none. Every 1.03 is read, and the most recent run of them, each within
    two years of the one before, is one case: its first filing is the petition. Item 1.03
    is filed again when the court confirms a plan, so when the run has more than one filing
    its last is reported as `plan_confirmed` ({date, accession, source_url}, else None):
    the case is no longer live from that date. A case whose last filing is more than three
    years old has been resolved: AAL's CIK still carries AMR's 2011 petition."""
    rows = sorted((r for r in list_8k_items(cik)
                   if r["items"] and "1.03" in r["items"] and r["filing_date"]),
                  key=lambda r: r["filing_date"])
    if not rows:
        return None
    days = [dt.date.fromisoformat(r["filing_date"][:10]) for r in rows]
    first = len(rows) - 1
    while first > 0 and (days[first] - days[first - 1]).days <= _CASE_GAP_DAYS:
        first -= 1
    if (_today() - days[-1]).days > _CASE_LOOKBACK_DAYS:
        return None
    case = rows[first:]
    # ponytail: a later 1.03 for affiliates joining the case reads as a confirmation too;
    # read the 8-K body to tell them apart if an issuer files one
    cite = [{"date": r["filing_date"], "accession": r["accession"],
             "source_url": r["source_url"]} for r in (case[0], case[-1])]
    return {**cite[0], "plan_confirmed": cite[1] if len(case) > 1 else None}


def petition_citation(pf: dict) -> Citation:
    """The citation behind a petition date from petition_filing."""
    return Citation(form_type="8-K", section="Item 1.03 — Bankruptcy or Receivership",
                    filing_date=pf.get("date"), accession_no=pf.get("accession"),
                    source_url=pf.get("source_url"),
                    quote="petition date proxied by the 8-K Item 1.03 filing date")


def crisis_triggers(cik, since: Optional[str] = None) -> list[dict]:
    """8-K filings carrying a crisis-of-confidence item (4.01/4.02/5.02). Rows with an
    unknown items list are still returned (triggers=None, items_unknown=True) so the
    caller reports "unknown", never "no trigger"."""
    out: list[dict] = []
    for r in list_8k_items(cik, since=since):
        if r["items_unknown"]:
            out.append({**r, "triggers": None})
            continue
        hits = {c: CRISIS_ITEMS[c] for c in r["items"] if c in CRISIS_ITEMS}
        if hits:
            out.append({**r, "triggers": hits})
    return out


def crisis_screen(triggers, liquidity, liquidity_events, accel=None) -> dict:
    """Crisis-of-confidence four-factor liquidity screen (Moyer, Distressed Debt Analysis,
    ch. 8). PURE — takes already-fetched inputs so it is unit-testable without network/DB.

    A restatement/fraud disclosure only becomes a *liquidity event* when it coincides
    with an immediate cash need the company cannot cover from cash on hand — Global
    Crossing survived on $2B of cash; WorldCom's refi wall did not. So the overall
    `crisis` flag = a trigger 8-K AND an at-risk near-term obligation not covered by cash.
    Revolver reliance (factor 2) and acceleration/MAC language (factor 3) amplify the
    need but are qualitative/best-effort, so they inform rather than gate the flag.
    """
    triggers = triggers or []
    liquidity = liquidity or {}
    liquidity_events = liquidity_events or []
    accel = accel or {}

    trig_map: dict = {}
    trigger_filings = []
    for t in triggers:
        if t.get("triggers"):
            trigger_filings.append(t)
            trig_map.update(t["triggers"])
    triggered = bool(trigger_filings)

    cash = liquidity.get("cash")
    undrawn = liquidity.get("undrawn_committed")
    cash_val = (cash or {}).get("value")
    undrawn_val = (undrawn or {}).get("value")
    reliance_pct = None
    if cash_val is not None and undrawn_val:
        denom = cash_val + undrawn_val
        reliance_pct = round(100.0 * undrawn_val / denom, 1) if denom > 0 else None

    # factor 4: immediate cash need — near events flagged at-risk / unfundable
    at_risk = [e for e in liquidity_events
               if any(f in (e.get("flags") or [])
                      for f in ("coupon_at_risk", "maturity_unfundable"))]
    need_total = sum(((e.get("amount") or {}).get("value") or 0.0) for e in at_risk)
    # tri-state: True (cash covers), False (cash < need), None (no at-risk event OR cash
    # not tagged). Don't assert "not covered" — or gate crisis — on an unknown cash figure.
    if not at_risk or cash_val is None:
        covered = None
    else:
        covered = cash_val >= need_total
    immediate_need = bool(at_risk) and covered is False

    accel_found = int(accel.get("clauses_found") or 0)
    crisis = triggered and immediate_need

    factors = {
        "cash": cash,
        "revolver": {
            "undrawn": undrawn, "reliance_pct": reliance_pct,
            "note": "a restatement/fraud disclosure typically trips a covenant and freezes "
                    "the revolver — reliance on undrawn capacity is fragile"},
        "acceleration": {
            "clauses_found": accel_found, "sample": accel.get("sample"),
            "available": bool(accel.get("available", True)),
            "note": "best-effort scan of the indexed covenant/notes corpus for cross-default / "
                    "material-adverse-change language — NOT full indenture text, and depends on "
                    "the LLM covenant extraction having run"},
        "immediate_need": {
            "events": at_risk[:3], "need_total": need_total, "covered_by_cash": covered,
            "note": "nearest coupon/maturity flagged at-risk or unfundable"},
    }
    return {
        "triggered": triggered, "trigger_items": sorted(trig_map),
        "trigger_filings": trigger_filings, "factors": factors, "crisis": crisis,
        "note": "crisis = a restatement/fraud 8-K trigger AND an immediate cash need not covered "
                "by cash on hand; revolver reliance and acceleration language amplify but are "
                "best-effort/qualitative.",
    }
