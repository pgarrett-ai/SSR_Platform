"""Pull readable text (MD&A + financial-statement notes) from a filing, and focus it down to
the passages that matter via keyword windowing — so the LLM sees targeted footnote text, not a
150k-character blob of statements.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

from .client import index_url_for

# Topic keywords used to route footnote text to the OBS extractor (brief §6b).
OBS_KEYWORDS = (
    "lease", "pension", "postretirement", "opeb", "retirement benefit",
    "supplier finance", "supply chain finance", "supply-chain finance", "payables facility",
    "factor", "securitiz", "transfer of financial assets", "receivables sold",
    "variable interest", "vie", "special purpose", "unconsolidated", "equity method",
    "guarantee", "take-or-pay", "take or pay", "purchase commitment", "purchase obligation",
    "unconditional purchase", "contingenc", "commitments", "environmental", "litigation",
    "related party", "off-balance", "nonrecourse", "non-recourse", "recourse", "joint venture",
)

DEBT_KEYWORDS = (
    "senior notes", "senior secured", "term loan", "revolving", "revolver", "indenture",
    "notes due", "credit facility", "aggregate principal", "interest rate", "matures",
    "maturity", "% notes", "debt consists", "long-term debt", "secured notes", "unsecured",
)


@dataclass
class FilingText:
    accession_no: str
    form_type: str
    filing_date: Optional[str]
    period_of_report: Optional[str]
    source_url: str
    mdna: str
    notes: str

    def obs_window(self, max_chars: int = 60000) -> str:
        return window_by_keywords(self.notes, OBS_KEYWORDS, max_chars=max_chars)

    def debt_window(self, max_chars: int = 28000) -> str:
        return window_by_keywords(self.notes, DEBT_KEYWORDS, max_chars=max_chars)


_SPACE_CHARS = "              　"
_SPACE_TABLE = {ord(c): " " for c in _SPACE_CHARS}


def _as_str(value) -> str:
    if not value:
        return ""
    # 10-K HTML extraction leaves non-breaking/figure spaces between table cells; normalize them
    # to plain spaces so the LLM reads "Total revenues 54,633" not "Total revenues\xa054,633".
    return str(value).translate(_SPACE_TABLE)


def _largest_item(obj, prefixes: tuple[str, ...]) -> str:
    """Return the longest item whose key starts with any prefix (handles Item 8 vs 8A/8B)."""
    best = ""
    try:
        items = list(obj.items)
    except Exception:
        items = []
    for key in items:
        if any(key.replace(" ", "").upper().startswith(p.replace(" ", "").upper()) for p in prefixes):
            try:
                txt = _as_str(obj[key])
            except Exception:
                txt = ""
            if len(txt) > len(best):
                best = txt
    return best


def get_filing_text(filing) -> Optional[FilingText]:
    """Extract MD&A + notes text from a 10-K/10-Q Filing object."""
    try:
        obj = filing.obj()
    except Exception:
        return None

    mdna = ""
    for attr in ("management_discussion",):
        try:
            mdna = _as_str(getattr(obj, attr, "")) or mdna
        except Exception:
            pass
    if not mdna:
        mdna = _largest_item(obj, ("Item 7",))

    # Notes / financial statements live in Item 8 (or 8A/8B for dual filers).
    notes = _largest_item(obj, ("Item 8",))
    if not notes:
        # 10-Q: financial statements are Item 1
        notes = _largest_item(obj, ("Item 1",))
    if not notes:
        # ATUS/TSE Item-8 gap: item keys didn't expose the statements. Fall back to the whole
        # filing plaintext — debt_window()/obs_window() keyword-carve it regardless of structure.
        try:
            notes = _as_str(filing.text())
        except Exception:
            notes = ""

    if not mdna and not notes:
        return None

    cik = str(getattr(filing, "cik", "") or "")
    acc = str(filing.accession_no)
    return FilingText(
        accession_no=acc,
        form_type=str(filing.form),
        filing_date=str(getattr(filing, "filing_date", "")) or None,
        period_of_report=str(getattr(filing, "period_of_report", "")) or None,
        source_url=getattr(filing, "url", None) or index_url_for(cik or "0", acc),
        mdna=mdna,
        notes=notes,
    )


def get_mdna_only(filing) -> Optional[FilingText]:
    """Lighter than get_filing_text: pull just the MD&A (Item 7 for 10-K, Item 2 for 10-Q) for the
    drift series, without parsing the heavy financial-statement notes for every quarterly filing."""
    try:
        obj = filing.obj()
    except Exception:
        return None
    mdna = ""
    try:
        mdna = _as_str(getattr(obj, "management_discussion", "")) or ""
    except Exception:
        mdna = ""
    if not mdna:
        # 10-K MD&A is Item 7; 10-Q MD&A is Item 2 (keyed "Part I, Item 2" — obj[key] resolves it).
        form = str(getattr(filing, "form", ""))
        keys = ("Item 7",) if form.startswith("10-K") else ("Item 2",)
        for k in keys:
            try:
                t = _as_str(obj[k])
            except Exception:
                t = ""
            if len(t) > len(mdna):
                mdna = t
    if not mdna or len(mdna) < 500:
        return None
    cik = str(getattr(filing, "cik", "") or "")
    acc = str(filing.accession_no)
    return FilingText(
        accession_no=acc,
        form_type=str(filing.form),
        filing_date=str(getattr(filing, "filing_date", "")) or None,
        period_of_report=str(getattr(filing, "period_of_report", "")) or None,
        source_url=getattr(filing, "url", None) or index_url_for(cik or "0", acc),
        mdna=mdna,
        notes="",
    )


def window_by_keywords(
    text: str, keywords, radius: int = 2800, max_chars: int = 60000
) -> str:
    """Extract merged ±radius windows around every keyword hit, capped at max_chars."""
    if not text:
        return ""
    lowered = text.lower()
    spans: list[tuple[int, int]] = []
    for kw in keywords:
        kwl = kw.lower()
        start = 0
        while True:
            i = lowered.find(kwl, start)
            if i == -1:
                break
            spans.append((max(0, i - radius), min(len(text), i + len(kw) + radius)))
            start = i + len(kw)
    if not spans:
        return ""
    spans.sort()
    merged = [list(spans[0])]
    for s, e in spans[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    out: list[str] = []
    total = 0
    for s, e in merged:
        chunk = text[s:e]
        if total + len(chunk) > max_chars:
            chunk = chunk[: max(0, max_chars - total)]
        if chunk:
            out.append(chunk)
            total += len(chunk)
        if total >= max_chars:
            break
    return "\n…\n".join(out)
