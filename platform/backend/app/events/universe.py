"""Daily universe refresh (plan §4 `universe` table). v1 populates exactly what
company_tickers.json gives — cik_str/ticker/title (URL+shape UNVERIFIED, ledger #1) —
plus lazy sic/exchange enrichment from submissions docs the poller fetches anyway
(fields UNVERIFIED, ledger #2). market_cap: Phase 7 yfinance batch, deliberately not here."""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from .. import models_events
from ..core.db import session_scope
from . import edgar_feed as feed

_TICKERS_JSON = "https://www.sec.gov/files/company_tickers.json"   # UNVERIFIED (ledger #1)


def refresh_universe() -> int:
    data = feed.get_json(_TICKERS_JSON, timeout=120.0)
    rows = list(data.values()) if isinstance(data, dict) else list(data)
    now = dt.datetime.utcnow()
    seen: set[str] = set()
    with session_scope() as session:
        for r in rows:
            raw = str(r.get("cik_str") or "").strip()
            if not raw.lstrip("0"):
                continue
            cik = feed.pad_cik(raw)
            if cik in seen:
                continue   # company_tickers.json is one row per TICKER: a CIK with units/
                           # warrants/multiple share classes repeats (VACI-UN + VACI-WT share
                           # one CIK). Autoflush is off, so session.get can't see the pending
                           # same-batch insert — dedupe here or the PK collides. First ticker wins.
            seen.add(cik)
            row = session.get(models_events.UniverseCompany, cik)
            if row is None:
                row = models_events.UniverseCompany(cik=cik)
                session.add(row)
            row.ticker = (r.get("ticker") or "").upper() or row.ticker
            row.name = r.get("title") or row.name
            row.is_active = True
            row.updated_at = now
        # dropped out of the file = delisted/deregistered: keep the row (events key on
        # CIK forever), just flag it inactive
        for row in session.query(models_events.UniverseCompany).filter_by(is_active=True):
            if row.cik not in seen:
                row.is_active = False
                row.updated_at = now
    return len(seen)


def enrich_from_submissions(session: Session, cik: str, data: dict) -> None:
    """Opportunistic sic/exchange/name fill from a submissions doc already in hand.
    Only fills blanks — company_tickers stays the authority for what it carries."""
    row = session.get(models_events.UniverseCompany, cik)
    if row is None:
        return
    row.sic = row.sic or (str(data.get("sic") or "") or None)
    ex = data.get("exchanges") or []
    row.exchange = row.exchange or (ex[0] if ex else None)
    row.name = row.name or data.get("name")
