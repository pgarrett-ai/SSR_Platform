"""Key reference rates — deterministic, keyless fetches from the NY Fed API (SOFR, EFFR)
and FRED's fredgraph.csv (Fed Funds target, prime, 3M T-bill, 10Y Treasury). Each
observation is stored per (series, date) in the `rates` table; the store is refreshed when
stale and served fail-soft — a dead source just means yesterday's rate with its date shown.

Note: credit agreements typically reference *Term* SOFR (CME-licensed, not free); overnight
SOFR is the free proxy and is labeled as such wherever a floater is resolved against it.
LIBOR ceased publication June 2023 — SOFR is the fallback successor.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import urllib.request
from typing import Optional

from sqlalchemy import select

from . import models

_UA = {"User-Agent": "distressed-credit-research-platform rates fetch"}
_TIMEOUT = 8.0
_STALE_AFTER = dt.timedelta(hours=12)

# key -> (display label, source, source arg)
SERIES: dict[str, tuple[str, str, str]] = {
    "SOFR": ("SOFR (overnight)", "nyfed", "secured/sofr"),
    "EFFR": ("Fed Funds (effective)", "nyfed", "unsecured/effr"),
    "DFEDTARU": ("Fed Funds target (upper)", "fred", "DFEDTARU"),
    "DPRIME": ("Prime rate", "fred", "DPRIME"),
    "DTB3": ("3M T-bill", "fred", "DTB3"),
    "DGS10": ("10Y Treasury", "fred", "DGS10"),
}

LIBOR_NOTE = ("LIBOR ceased publication June 2023; SOFR (+ the contractual spread "
              "adjustment) is the successor reference rate.")


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def parse_nyfed(payload: str) -> Optional[tuple[str, float]]:
    """markets.newyorkfed.org rates JSON -> (date, percent) for the latest observation."""
    rows = (json.loads(payload).get("refRates") or [])
    if not rows:
        return None
    r = rows[0]
    try:
        return str(r["effectiveDate"]), float(r["percentRate"])
    except (KeyError, TypeError, ValueError):
        return None


def parse_fred_csv(payload: str) -> Optional[tuple[str, float]]:
    """fredgraph.csv (DATE,VALUE) -> (date, value) for the latest non-missing row ('.')."""
    rows = list(csv.reader(io.StringIO(payload)))
    for row in reversed(rows[1:]):
        if len(row) < 2:
            continue
        try:
            return row[0], float(row[1])
        except ValueError:
            continue
    return None


def _fetch_latest(key: str) -> Optional[tuple[str, float]]:
    label, source, arg = SERIES[key]
    if source == "nyfed":
        url = f"https://markets.newyorkfed.org/api/rates/{arg}/last/1.json"
        return parse_nyfed(_get(url))
    start = (dt.date.today() - dt.timedelta(days=21)).isoformat()
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={arg}&cosd={start}"
    return parse_fred_csv(_get(url))


def refresh_if_stale(session) -> None:
    """Fetch fresh observations when the newest stored fetch is older than _STALE_AFTER.
    Per-series fail-soft: one dead source never blocks the others or the request."""
    newest = session.scalars(
        select(models.Rate.fetched_at).order_by(models.Rate.fetched_at.desc()).limit(1)
    ).first()
    now = dt.datetime.now(dt.timezone.utc)
    if newest:
        try:
            if now - dt.datetime.fromisoformat(newest) < _STALE_AFTER:
                return
        except ValueError:
            pass
    for key in SERIES:
        try:
            latest = _fetch_latest(key)
        except Exception:
            continue   # fail-soft: keep serving the stored observation
        if latest is None:
            continue
        date, value = latest
        session.merge(models.Rate(series=key, date=date, value=value,
                                  fetched_at=now.isoformat()))
    session.flush()   # SessionLocal is autoflush=False — same-request reads must see these


def get_key_rates(session) -> list[dict]:
    """Latest stored observation per series, in SERIES display order."""
    out = []
    for key, (label, _src, _arg) in SERIES.items():
        row = session.scalars(
            select(models.Rate).where(models.Rate.series == key)
            .order_by(models.Rate.date.desc()).limit(1)
        ).first()
        if row is not None:
            out.append({"series": key, "label": label, "value": row.value, "date": row.date})
    return out
