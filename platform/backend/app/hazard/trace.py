"""Per-issuer bond quotes (last trade price/yield/date) from a local drop file."""
from __future__ import annotations

import json
from pathlib import Path

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
