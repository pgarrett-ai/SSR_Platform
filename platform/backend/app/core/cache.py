"""Pre-computed overview cache (brief §3 'demo safety').

A full live run makes several Claude calls + large EDGAR downloads (~3 min). For the demo, hero
names are pre-built into JSON snapshots and served instantly; a "Run live" toggle bypasses the
cache for a real re-run. Successful live runs are also written back so repeat requests are fast.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .config import CACHE_DIR, get_settings
from ..schemas import Overview

# Trust boundary: `ticker` is request-derived and is interpolated into cache FILENAMES
# (cache_path, _hazard_section, load_latest_overview, refi.hazard_inputs). The allowlist
# excludes path separators ('/' and '\') so a value can never form '../' or '..\' and
# escape CACHE_DIR; '..' alone is also rejected for clarity. Output stays strip().upper()
# so cache/DB keys are unchanged, and the charset is no stricter than EdgarClient.resolve_company
# (letters/digits for symbols + aliases, dot for BRK.A, hyphen; up to 16 to fit 'CIK'+10-digit).
_TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,16}$")


def safe_ticker(ticker: str) -> str:
    """Normalize (strip/upper) and validate a request-derived ticker before it reaches any
    filesystem path. Raises ValueError on anything that could traverse or is malformed."""
    t = (ticker or "").strip().upper()
    if ".." in t or not _TICKER_RE.match(t):
        raise ValueError(f"invalid ticker: {ticker!r}")
    return t


def cache_path(ticker: str, years: int) -> Path:
    return CACHE_DIR / f"{safe_ticker(ticker)}_{int(years)}y.json"


def load_overview(ticker: str, years: int) -> Optional[Overview]:
    try:
        p = cache_path(ticker, years)
    except ValueError:
        return None   # invalid ticker -> treat as no cache; the live path resolves it and 404s
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
    try:
        t = safe_ticker(ticker)
    except ValueError:
        return None
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
