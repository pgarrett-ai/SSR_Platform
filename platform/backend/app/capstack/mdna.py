"""MD&A section retention — pull the MD&A text for each 10-K/10-Q in the window and store
it per period so the UI can render the actual discussion (latest + history). Deterministic:
no scoring, no LLM — the analyst reads the text."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

from ..edgar.documents import get_mdna_only

_MAX_PERIODS = 9


@dataclass
class MdnaPeriod:
    accession: str
    form_type: str
    period_end: Optional[dt.date]
    filing_date: Optional[str]
    source_url: Optional[str]
    text: str


def _as_date(s) -> Optional[dt.date]:
    if not s:
        return None
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def build_mdna_series(company, years: int) -> list[MdnaPeriod]:
    """Pull MD&A text for 10-K/10-Q in the window, most recent _MAX_PERIODS, ascending by period."""
    today = dt.date.today()
    start = today.replace(year=today.year - max(1, years))
    try:
        filings = company.get_filings(form=["10-K", "10-Q"]).filter(
            date=f"{start.isoformat()}:{today.isoformat()}"
        )
    except Exception:
        return []

    periods: list[MdnaPeriod] = []
    seen_periods = set()
    # filings come newest-first; collect until we have enough distinct periods
    for f in filings:
        if len(periods) >= _MAX_PERIODS:
            break
        pe = _as_date(getattr(f, "period_of_report", None))
        if pe in seen_periods:
            continue
        ft = get_mdna_only(f)
        if ft is None:
            continue
        seen_periods.add(pe)
        periods.append(MdnaPeriod(
            accession=ft.accession_no,
            form_type=ft.form_type,
            period_end=pe,
            filing_date=ft.filing_date,
            source_url=ft.source_url,
            text=ft.mdna,
        ))
    # sort ascending by period end so the newest period is last
    periods.sort(key=lambda p: (p.period_end or dt.date.min))
    return periods
