"""Per-CIK 8-K item enumeration (Moyer ch. 8/12 event detection).

Uses the EDGAR **submissions API** (`data.sec.gov/submissions/CIK{10}.json`),
which is CIK-scoped by construction and carries each 8-K's `items` codes, filing
date, and accession in a single request — no full-text-search phrase queries, no
pagination, none of the market-wide fragility. An empty/absent items list is
reported as `items_unknown`, never silently treated as "no trigger" (Phase-3
decision #8).

Reused by F1 (petition date, `petition_filing`) and F3 (crisis triggers,
`crisis_triggers`). `_http_get_submissions` is the raw network seam; `_fetch_submissions`
wraps it with a 1h per-CIK disk cache + retry. Tests monkeypatch either.
"""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Optional

from ..core.config import CACHE_DIR, get_settings

_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik10}.json"
_SUBMISSIONS_CACHE_DIR = CACHE_DIR / "eightk"   # per-CIK cache; module global so tests can redirect it
_SUBMISSIONS_TTL_S = 3600   # 1h — collapses page-mount refetch storms, caps petition-detection lag at ~1h

# crisis-of-confidence 8-K items (Moyer ch. 8): restatement / auditor / management.
# 4.01/4.02 are the accounting-confidence signals; 5.02 also covers routine appointments
# (Item 5.02 = Departure/Election/Appointment), so it is noisier — labeled accurately and
# treated as secondary. A body-keyword read to isolate genuine departures is the upgrade path.
CRISIS_ITEMS = {
    "4.01": "auditor change",
    "4.02": "non-reliance / restatement",
    "5.02": "officer/director change",
}


def _http_get_submissions(cik) -> dict:
    """Raw network seam — the only outbound call; monkeypatched in tests."""
    url = _SUBMISSIONS.format(cik10=str(cik).lstrip("0").zfill(10))
    req = urllib.request.Request(url, headers={"User-Agent": get_settings().sec_user_agent})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _fetch_submissions(cik) -> dict:
    """Submissions JSON with a per-CIK disk cache (1h TTL) + light retry. The cache keeps
    repeated /recovery/case & /recovery/crisis calls from re-hitting EDGAR (SEC's 10 req/s
    fair-access limit → IP ban); the 1h TTL still surfaces a same-day petition/restatement 8-K
    within the hour. (Tests monkeypatch this whole function, so the cache/retry only run live.)"""
    cik10 = str(cik).lstrip("0").zfill(10)
    p = _SUBMISSIONS_CACHE_DIR / f"{cik10}.json"
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
            data = _http_get_submissions(cik)
            break
        except Exception as exc:   # transient 429/5xx/timeout — short backoff, then degrade
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


def crisis_screen(triggers, liquidity, liquidity_events, accel=None) -> dict:
    """Crisis-of-confidence four-factor liquidity screen (Moyer ch. 8). PURE — takes
    already-fetched inputs so it is unit-testable without network/DB.

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
                    "the revolver — reliance on undrawn capacity is fragile (Moyer ch. 8 factor 2)"},
        "acceleration": {
            "clauses_found": accel_found, "sample": accel.get("sample"),
            "available": bool(accel.get("available", True)),
            "note": "best-effort scan of the indexed covenant/notes corpus for cross-default / "
                    "material-adverse-change language — NOT full indenture text, and depends on "
                    "the LLM covenant extraction having run (Moyer ch. 8 factor 3)"},
        "immediate_need": {
            "events": at_risk[:3], "need_total": need_total, "covered_by_cash": covered,
            "note": "nearest coupon/maturity flagged at-risk or unfundable (Moyer ch. 8 factor 4)"},
    }
    return {
        "triggered": triggered, "trigger_items": sorted(trig_map),
        "trigger_filings": trigger_filings, "factors": factors, "crisis": crisis,
        "note": "crisis = a restatement/fraud 8-K trigger AND an immediate cash need not covered "
                "by cash on hand; revolver reliance and acceleration language amplify but are "
                "best-effort/qualitative (Moyer ch. 8).",
    }
