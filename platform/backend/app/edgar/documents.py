"""Pull readable text (MD&A + financial-statement notes) from a filing, and focus it down to
the passages that matter via keyword windowing — so the LLM sees targeted footnote text, not a
150k-character blob of statements.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Optional

from .client import index_url_for

QUOTE_NOT_FOUND = "quote not found verbatim in source"
# Curly quotes/dashes, and the cp1252 bytes older exhibits decode to C1 controls ("\x93Lender\x94").
_QUOTE_FOLD = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                             "\x91": "'", "\x92": "'", "\x93": '"', "\x94": '"',
                             "\x96": "-", "\x97": "-"})
# Quotation marks (an apostrophe inside a word stays), whitespace, soft hyphens and table
# rules ('|', box drawing): HTML-to-text rendering adds, drops or moves all of these.
_QUOTE_DROP = re.compile(r"\"|(?<!\w)'|'(?!\w)|[\s|\u2500-\u257f\xad\u200b]+")
# Hanging-indent list items render as two columns, so a clause label can land mid-sentence
# ("excluded\n  (h)  from the definition"); the label is the line's first token, then a gap.
_COLUMN_LABEL = re.compile(
    r"^[ \t]*(?:\((?:[a-z]{1,4}|[ivxlc]{1,6}|[A-Z]|\d{1,2})\)"
    r"|(?:[a-z]{1,2}|[ivxlc]{1,6}|[A-Z]{1,4}|\d{1,2})\.)(?=[ \t]{2,}\S)", re.M)
# Whitespace between two digits separates two numbers ("2.2 4" is not "2.24"): keep a marker.
_DIGIT_GAP = re.compile(r"(?<=\d)[\s|\u2500-\u257f\xad\u200b]+(?=\d)")


def _fold(s: str) -> str:
    s = _DIGIT_GAP.sub("\x00", s.translate(_QUOTE_FOLD))
    return _QUOTE_DROP.sub("", s).lower()


def _in_order(frags: list[str], t: str) -> bool:
    pos = 0
    for f in frags:
        pos = t.find(f, pos)
        if pos < 0:
            return False
        pos += len(f)
    return True


def quote_in_text(quote: Optional[str], text: Optional[str], list_labels: bool = False) -> bool:
    """Is an LLM's 'verbatim' quote really in the text it read? Case, curly quotes/dashes,
    quotation marks, whitespace and table rules are folded away, so the words, digits and
    other punctuation must match exactly; with list_labels (legal agreements), a clause label
    the text renders mid-sentence may be read through; a spliced quote ('A ... B') must match as fragments in order. An empty
    quote is never found."""
    frags = [f.strip(".,;:") for f in re.split(r"\.\.\.|…", _fold(quote or ""))]
    frags = [f for f in frags if f]
    text = text or ""
    return bool(frags) and (_in_order(frags, _fold(text))
                            or (list_labels
                                and _in_order(frags, _fold(_COLUMN_LABEL.sub("", text)))))


# Topic keywords used to route footnote text to the OBS extractor.
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
        return window_by_keywords(self.notes, DEBT_KEYWORDS, max_chars=max_chars,
                                  densest_first=True)


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
    text: str, keywords, radius: int = 2800, max_chars: int = 60000,
    densest_first: bool = False,
) -> str:
    """Extract merged ±radius windows around every keyword hit, capped at max_chars.
    With `densest_first`, an over-cap text also keeps the windows with the most hits per
    character, up to another max_chars: the earliest windows alone can be a 10-Q's
    statement captions ('Long-term debt', one hit each) that fill the cap before the debt
    note's instrument table. Output stays in document order."""
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
    merged = [[*spans[0], 1]]   # [start, end, hits]
    for s, e in spans[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
            merged[-1][2] += 1
        else:
            merged.append([s, e, 1])
    orders = [merged]
    if densest_first:
        orders.append(sorted(merged, key=lambda m: m[2] / (m[1] - m[0]), reverse=True))
    kept: dict[int, int] = {}   # window start -> end; each order fills its own max_chars
    for order in orders:
        room = max_chars
        for s, e, _ in order:
            if room <= 0:
                break
            end = min(e, s + room)
            kept[s] = max(kept.get(s, s), end)
            room -= end - s
    return "\n…\n".join(text[s:e] for s, e in sorted(kept.items()))
