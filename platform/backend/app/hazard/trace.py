"""Per-issuer bond marks (last trade price/yield/date) from a local marks file.

Marks come from the user's own pricing source and are never committed:
data/bond_quotes.json, shaped like data/bond_quotes.example.json (ticker -> bonds with
cusip, coupon, maturity, last_price, last_yield, last_trade, as_of). Without the file,
every price-dependent view shows "no marks" and nothing else changes.
"""
from __future__ import annotations

import json
from pathlib import Path

_QUOTES_PATH = Path(__file__).resolve().parent / "data" / "bond_quotes.json"


def get_issuer_bonds(ticker: str) -> dict:
    """Per-issuer bond marks from the local marks file; graceful when absent."""
    try:
        quotes = json.loads(_QUOTES_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"enabled": False, "note": "no marks file (data/bond_quotes.json)"}
    except Exception as e:
        return {"enabled": False, "note": f"bond_quotes.json unreadable: {e}"}
    bonds = quotes.get(ticker.upper()) or []
    if not bonds:
        return {"enabled": False, "note": f"no marks for {ticker.upper()}"}
    return {"enabled": True, "bonds": bonds,
            "as_of": max(b.get("as_of", "") for b in bonds),
            "note": "last-trade marks from the local marks file"}
