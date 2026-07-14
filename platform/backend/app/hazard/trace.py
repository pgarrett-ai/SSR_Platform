"""FINRA TRACE access via the Query API (direct requests — no third-party package).

Currently powers the **corporate-credit market backdrop**: high-yield vs investment-grade market
breadth (advances vs declines) -> a risk-off/neutral/risk-on regime read. This is the only
TRACE data available on the FINRA *free* Query API (market aggregates).

Per-issuer bond pricing (individual trades by CUSIP/symbol) lives in a different, entitled dataset
whose exact group/name we still need from the FINRA console — once known, `get_issuer_bonds`
below is the place to wire it (auth + `_fetch` already work).

Graceful: no creds -> disabled; any failure -> note + pipeline continues. Cached per day.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from ..core.config import get_settings

_TOKEN_URL = ("https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token"
              "?grant_type=client_credentials")
_DATA_URL = "https://api.finra.org/data/group/{group}/name/{name}"
_BREADTH = ("fixedIncomeMarket", "corporateMarketBreadth")


def _token(s) -> str:
    r = requests.post(_TOKEN_URL, auth=(s.finra_api_key, s.finra_api_secret), timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def _fetch(token: str, group: str, name: str, payload: dict) -> pd.DataFrame:
    r = requests.post(
        _DATA_URL.format(group=group, name=name),
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        json=payload, timeout=60)
    r.raise_for_status()
    return pd.DataFrame(r.json())


# --------------------------------------------------------------------------- #
# Credit backdrop (working)
# --------------------------------------------------------------------------- #
@dataclass
class CreditBackdrop:
    enabled: bool = False
    note: str = ""
    as_of: Optional[str] = None
    hy_advances: Optional[int] = None
    hy_declines: Optional[int] = None
    hy_breadth: Optional[float] = None       # advances / (advances + declines), 0..1
    hy_volume_share: Optional[float] = None  # HY volume / all-securities volume
    ig_breadth: Optional[float] = None
    signal: Optional[str] = None             # risk-off / neutral / risk-on


def _breadth(adv, dec) -> Optional[float]:
    tot = (adv or 0) + (dec or 0)
    return None if tot == 0 else adv / tot


def _signal(breadth: Optional[float]) -> str:
    if breadth is None:
        return "neutral"
    return "risk-off" if breadth < 0.45 else "risk-on" if breadth > 0.55 else "neutral"


def get_credit_backdrop() -> CreditBackdrop:
    return _backdrop_for_day(dt.date.today().isoformat())


@lru_cache(maxsize=4)
def _backdrop_for_day(day: str) -> CreditBackdrop:
    s = get_settings()
    if not s.trace_enabled:
        return CreditBackdrop(note="TRACE feed not configured (FINRA API credentials not set)")
    try:
        token = _token(s)
        end = dt.date.fromisoformat(day)
        start = end - dt.timedelta(days=45)
        df = _fetch(token, *_BREADTH, {
            "limit": 500,
            "dateRangeFilters": [{"startDate": start.isoformat(), "endDate": end.isoformat(),
                                  "fieldName": "tradeReportDate"}],
        })
        if df.empty or "tradeReportDate" not in df.columns:
            return CreditBackdrop(note="TRACE returned no recent corporate-debt breadth data")

        latest = df["tradeReportDate"].astype(str).max()
        rows = df[df["tradeReportDate"].astype(str) == latest]
        by_cat = {str(r["productCategory"]).lower(): r for _, r in rows.iterrows()}
        hy, ig, allsec = by_cat.get("high yield"), by_cat.get("investment grade"), by_cat.get("all securities")
        if hy is None:
            return CreditBackdrop(note="TRACE: no high-yield breadth row in latest data")

        hy_adv, hy_dec = int(hy["advances"]), int(hy["declines"])
        breadth = _breadth(hy_adv, hy_dec)
        share = (float(hy["totalVolume"]) / float(allsec["totalVolume"])
                 if allsec is not None and float(allsec["totalVolume"]) > 0 else None)
        return CreditBackdrop(
            enabled=True, as_of=latest,
            hy_advances=hy_adv, hy_declines=hy_dec, hy_breadth=breadth, hy_volume_share=share,
            ig_breadth=_breadth(int(ig["advances"]), int(ig["declines"])) if ig is not None else None,
            signal=_signal(breadth),
        )
    except Exception as e:
        return CreditBackdrop(note=f"TRACE fetch failed: {e}")


# --------------------------------------------------------------------------- #
# Per-issuer bond quotes (browser-scraped drop file)
# --------------------------------------------------------------------------- #
# There is no free API for per-issue TRACE data (proven 2026-06-30), but the public bond
# pages render it: finra.org/finra-data/fixed-income/bond?symbol=…|cusip=…&bondType=CA
# (last trade price/yield/date, coupon, maturity, ratings — in the finra-dr-accordion DOM).
# The page's own historical-series API (services-dynarep …/EndOfDayPriceYield) is
# session-authorized and 401s outside the app, so v1 is a scrape-refreshed drop file:
# refresh data/bond_quotes.json via a browser session per bond (agent-assisted), and this
# reader serves it. ponytail: manual refresh cadence; Playwright automation if the bond
# list outgrows a hand-refresh.
_QUOTES_PATH = Path(__file__).resolve().parent / "data" / "bond_quotes.json"


def get_issuer_bonds(ticker: str) -> dict:
    """Per-issuer bond quotes from the scraped drop file; graceful when absent."""
    try:
        quotes = json.loads(_QUOTES_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"enabled": False, "note": "no bond_quotes.json (scrape a bond page to seed it)"}
    except Exception as e:
        return {"enabled": False, "note": f"bond_quotes.json unreadable: {e}"}
    bonds = quotes.get(ticker.upper()) or []
    if not bonds:
        return {"enabled": False, "note": f"no scraped bonds for {ticker.upper()}"}
    return {"enabled": True, "bonds": bonds,
            "as_of": max(b.get("as_of", "") for b in bonds),
            "note": "last-trade quotes scraped from finra.org bond pages"}
