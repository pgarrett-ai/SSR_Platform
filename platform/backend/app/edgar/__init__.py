"""The ONE EDGAR module: retrieval (client), XBRL facts (facts), document text (documents),
paced raw transport (http).

Exposes both styles the merged apps use: capstack's `EdgarClient` class and hazard's
module-level functions (thin wrappers over the same client).
"""
from __future__ import annotations

from typing import Optional

from .client import (
    EdgarClient,
    ExhibitInfo,
    FilingInfo,
    NoFilingsError,
    TickerNotFoundError,
    index_url_for,
    timeline_filings,
)
from .facts import (
    FinancialSeries,
    YearFacts,
    build_financial_series,
    raw_value,
    source_url,
)
from .http import paced_get


def resolve_company(ticker: str):
    return EdgarClient().resolve_company(ticker)


def current_ticker(company) -> Optional[str]:
    return EdgarClient.current_ticker(company)


__all__ = [
    "EdgarClient",
    "ExhibitInfo",
    "FilingInfo",
    "NoFilingsError",
    "TickerNotFoundError",
    "index_url_for",
    "timeline_filings",
    "resolve_company",
    "current_ticker",
    "FinancialSeries",
    "YearFacts",
    "build_financial_series",
    "raw_value",
    "source_url",
    "paced_get",
]
