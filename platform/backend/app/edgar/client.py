"""EDGAR retrieval: ticker -> CIK, fetch filings + exhibits over an N-year window.

Thin wrapper over `edgartools`. edgartools handles SEC rate-limiting/caching internally;
we set the required identity (name + email) once at import time.
"""
from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass, field
from typing import Optional

from edgar import Company, set_identity

from ..core.config import get_settings

_identity_lock = threading.Lock()
_identity_set = False

# Forms we pull into the window. 10-K/10-Q carry XBRL + MD&A + footnotes; 8-K carries
# credit-agreement / LME exhibits; S-1/S-4 carry indentures.
DEFAULT_FORMS = ["10-K", "10-Q", "8-K", "S-1", "S-4"]

# Exhibit types that are candidate credit documents (brief §5).
CREDIT_EXHIBIT_PREFIXES = ("EX-10", "EX-4")

# Legacy / delisted / renamed tickers the SEC's current company_tickers.json no longer maps.
# Distressed issuers frequently rename, move to OTC, or get a bankruptcy "Q" ticker — so a
# real tool can't rely on the live ticker file alone. Keyed by the symbol an analyst would type.
TICKER_ALIASES: dict[str, int] = {
    "ATUS": 1702780,   # Altice USA -> renamed "Optimum Communications, Inc." (now OPTU)
    "TSE": 1519061,    # Trinseo PLC -> now TSEOQ (Chapter 11)
    "QVCGA": 1355096,  # QVC Group, Inc. -> now QVCGQ (Chapter 11)
    "QVC": 1355096,
}

# Technical XBRL / rendering attachments we never treat as readable exhibits.
_SKIP_EXHIBIT_PREFIXES = ("EX-101", "EX-104", "GRAPHIC", "XML", "EX-100")


class TickerNotFoundError(Exception):
    pass


class NoFilingsError(Exception):
    pass


@dataclass
class ExhibitInfo:
    exhibit_type: Optional[str]
    description: Optional[str]
    document: Optional[str]
    url: Optional[str]
    is_credit_doc: bool = False


@dataclass
class FilingInfo:
    accession_no: str
    form_type: str
    filing_date: Optional[dt.date]
    period_of_report: Optional[dt.date]
    primary_doc_url: Optional[str]
    filing_index_url: Optional[str]
    exhibits: list[ExhibitInfo] = field(default_factory=list)

    @property
    def n_credit_docs(self) -> int:
        return sum(1 for e in self.exhibits if e.is_credit_doc)


def _ensure_identity() -> None:
    global _identity_set
    if _identity_set:
        return
    with _identity_lock:
        if not _identity_set:
            set_identity(get_settings().sec_user_agent)
            _identity_set = True


def _as_date(value) -> Optional[dt.date]:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _classify_exhibit(doc_type: Optional[str]) -> bool:
    if not doc_type:
        return False
    up = doc_type.upper()
    return any(up.startswith(p) for p in CREDIT_EXHIBIT_PREFIXES)


def _skip_exhibit(doc_type: Optional[str]) -> bool:
    if not doc_type:
        return False
    up = doc_type.upper()
    return any(up.startswith(p) for p in _SKIP_EXHIBIT_PREFIXES)


class EdgarClient:
    """Resolve issuers and pull filings. All network access goes through edgartools."""

    def __init__(self) -> None:
        _ensure_identity()

    # ---- issuer resolution -------------------------------------------------
    def resolve_company(self, ticker: str) -> Company:
        """Resolve a user-typed symbol to an EDGAR Company. Handles raw CIKs and
        legacy/renamed tickers (common for distressed issuers) via an alias map."""
        symbol = ticker.strip().upper()

        # 1) Raw CIK (e.g. "1702780" or "CIK0001702780").
        digits = symbol.lstrip("CIK").lstrip("0") or "0"
        if symbol.replace("CIK", "").lstrip("0").isdigit() and digits.isdigit():
            company = self._safe_company(int(digits))
            if company is not None:
                return company

        # 2) Alias map for delisted/renamed/bankruptcy-"Q" symbols.
        if symbol in TICKER_ALIASES:
            company = self._safe_company(TICKER_ALIASES[symbol])
            if company is not None:
                return company

        # 3) Direct ticker via SEC's current ticker file.
        company = self._safe_company(symbol)
        if company is not None:
            return company

        raise TickerNotFoundError(
            f"Could not resolve '{ticker}' to an EDGAR issuer. It may be delisted, renamed, "
            f"or in bankruptcy (a 'Q' ticker). Try the issuer's CIK number instead."
        )

    @staticmethod
    def _safe_company(identifier) -> Optional[Company]:
        try:
            company = Company(identifier)
        except Exception:
            return None
        if company is None or not getattr(company, "cik", None):
            return None
        return company

    @staticmethod
    def current_ticker(company: Company) -> Optional[str]:
        tickers = getattr(company, "tickers", None)
        if tickers:
            return str(tickers[0])
        return None

    # ---- filings -----------------------------------------------------------
    def get_filings_in_window(
        self,
        company: Company,
        years: int,
        forms: Optional[list[str]] = None,
        include_exhibits: bool = True,
    ) -> list[FilingInfo]:
        forms = forms or DEFAULT_FORMS
        today = dt.date.today()
        start = today.replace(year=today.year - max(1, years))
        date_filter = f"{start.isoformat()}:{today.isoformat()}"

        try:
            filings = company.get_filings(form=forms).filter(date=date_filter)
        except Exception as exc:
            raise NoFilingsError(f"EDGAR filing query failed: {exc}") from exc

        results: list[FilingInfo] = []
        for f in filings:
            info = FilingInfo(
                accession_no=str(f.accession_no),
                form_type=str(f.form),
                filing_date=_as_date(getattr(f, "filing_date", None)),
                period_of_report=_as_date(getattr(f, "period_of_report", None)),
                primary_doc_url=getattr(f, "document_url", None) or getattr(f, "url", None),
                filing_index_url=getattr(f, "url", None),
            )
            if include_exhibits:
                info.exhibits = self._extract_exhibits(f)
            results.append(info)

        if not results:
            raise NoFilingsError(
                f"No {'/'.join(forms)} filings for {company.name} in the last {years} year(s)."
            )
        return results

    def _extract_exhibits(self, filing) -> list[ExhibitInfo]:
        out: list[ExhibitInfo] = []
        try:
            attachments = filing.attachments
        except Exception:
            return out
        for a in attachments:
            doc_type = getattr(a, "document_type", None)
            if _skip_exhibit(doc_type):
                continue
            out.append(
                ExhibitInfo(
                    exhibit_type=doc_type,
                    description=str(getattr(a, "description", "") or "")[:300],
                    document=getattr(a, "document", None),
                    url=getattr(a, "url", None),
                    is_credit_doc=_classify_exhibit(doc_type),
                )
            )
        return out


# Forms for the hazard event timeline. 10-K/10-Q carry the XBRL facts; 8-K flags events
# (Item 1.03 = bankruptcy, the future default-label source).
TIMELINE_FORMS = ["10-K", "10-Q", "8-K"]


def timeline_filings(company: Company, years: int) -> list[dict]:
    """Simple newest-first filing dicts for the hazard event timeline (no exhibits, no raise)."""
    try:
        infos = EdgarClient().get_filings_in_window(
            company, years, forms=TIMELINE_FORMS, include_exhibits=False
        )
    except Exception:
        return []
    out = [
        {
            "accession_no": f.accession_no,
            "form_type": f.form_type,
            "filing_date": f.filing_date,
            "url": f.filing_index_url or f.primary_doc_url,
        }
        for f in infos
    ]
    out.sort(key=lambda x: x["filing_date"] or dt.date.min, reverse=True)
    return out


def index_url_for(cik: str | int, accession_no: str) -> str:
    """Canonical EDGAR filing-index URL from CIK + accession (for citing facts whose
    Filing object we didn't fetch directly)."""
    acc_nodash = accession_no.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{acc_nodash}/{accession_no}-index.htm"
    )
