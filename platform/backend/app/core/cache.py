"""Pre-computed overview cache (brief §3 'demo safety').

A full live run makes several Claude calls + large EDGAR downloads (~3 min). For the demo, hero
names are pre-built into JSON snapshots and served instantly; a "Run live" toggle bypasses the
cache for a real re-run. Successful live runs are also written back so repeat requests are fast.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .config import CACHE_DIR, get_settings
from ..schemas import Overview


def cache_path(ticker: str, years: int) -> Path:
    return CACHE_DIR / f"{ticker.strip().upper()}_{int(years)}y.json"


def load_overview(ticker: str, years: int) -> Optional[Overview]:
    p = cache_path(ticker, years)
    if not p.exists():
        return None
    try:
        ov = Overview.model_validate_json(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    ov.header.from_cache = True
    return ov


def load_latest_overview(ticker: str) -> Optional[Overview]:
    """Any cached snapshot for the ticker regardless of years window (newest file wins).
    Cache-only by design: callers use this for cheap cross-module reads, never a live run."""
    t = ticker.strip().upper()
    for p in sorted(CACHE_DIR.glob(f"{t}_*y.json"),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            ov = Overview.model_validate_json(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        ov.header.from_cache = True
        return ov
    return None


def save_overview(ticker: str, years: int, overview: Overview) -> None:
    try:
        cache_path(ticker, years).write_text(
            overview.model_dump_json(indent=2), encoding="utf-8"
        )
        # keep the screening index in step with the snapshot (lazy import: no cycle)
        from ..store import upsert_snapshot
        from .db import session_scope
        with session_scope() as session:
            upsert_snapshot(session, ticker.strip().upper(), overview)
    except Exception:
        pass  # caching is best-effort; never fail a request over it


def is_hero(ticker: str) -> bool:
    return ticker.strip().upper() in get_settings().hero_ticker_set


def cached_tickers() -> list[str]:
    out = []
    for p in CACHE_DIR.glob("*_*y.json"):
        out.append(p.stem)
    return sorted(out)
