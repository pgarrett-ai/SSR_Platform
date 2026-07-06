"""MD&A semantic drift (brief §7) — EDGAR-only, experimental.

For each 10-K/10-Q in the window we pull the MD&A, compute period-over-period cosine distance
with a local TF-IDF vectorizer (no paid embedding vendor, no torch), and overlay a single
zero-shot Claude stress score of how management frames liquidity / covenant compliance / going
concern. A sudden jump in drift, or a rising stress trend, often precedes guidance cuts. We track
the trend, not the absolute level.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

from ..edgar.documents import get_mdna_only, window_by_keywords
from ..core.llm import extract_structured
from ..schemas import DriftPoint

_LIQUIDITY_KEYWORDS = (
    "liquidity", "going concern", "substantial doubt", "covenant", "capital resources",
    "refinanc", "maturit", "sufficient", "ability to continue", "funding", "cash on hand",
    "borrowing", "amend", "forbear", "default",
)

_MAX_PERIODS = 9


@dataclass
class MdnaPeriod:
    accession: str
    form_type: str
    period_end: Optional[dt.date]
    filing_date: Optional[str]
    source_url: Optional[str]
    text: str
    drift_from_prior: Optional[float] = None
    tone: Optional[float] = None


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
    # sort ascending by period end (fallback filing date) so drift is period-over-period
    periods.sort(key=lambda p: (p.period_end or dt.date.min))
    return periods


def _compute_drift(periods: list[MdnaPeriod]) -> None:
    if len(periods) < 2:
        return
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except Exception:
        return
    docs = [p.text for p in periods]
    try:
        vec = TfidfVectorizer(stop_words="english", max_features=4000, ngram_range=(1, 2))
        X = vec.fit_transform(docs)
    except Exception:
        return
    for i in range(1, len(periods)):
        try:
            sim = float(cosine_similarity(X[i - 1], X[i])[0, 0])
            periods[i].drift_from_prior = round(1.0 - sim, 4)
        except Exception:
            periods[i].drift_from_prior = None


_TONE_SYSTEM = (
    "You are a distressed-credit analyst scoring how management FRAMES liquidity, covenant "
    "compliance and going-concern risk in MD&A excerpts. Score only the tone/framing in the text "
    "provided, not your outside knowledge. 0 = confident, ample liquidity, comfortable covenant "
    "headroom; 50 = cautious/hedged; 100 = substantial-doubt / going-concern language, covenant "
    "breach or forbearance, severe distress."
)

_TONE_TOOL_DESC = (
    "Return one stress score (0-100) per period based solely on management's liquidity / covenant / "
    "going-concern framing in that period's excerpt. Higher = more stressed framing."
)

_TONE_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "period": {"type": "string"},
                    "overall_stress": {"type": "number"},
                    "going_concern": {"type": "boolean"},
                    "note": {"type": "string"},
                },
                "required": ["period", "overall_stress"],
            },
        }
    },
    "required": ["scores"],
}


def _score_tone(periods: list[MdnaPeriod], llm_enabled: bool) -> None:
    if not llm_enabled or not periods:
        return
    # one batched call over all periods' liquidity windows (capped upstream at _MAX_PERIODS)
    recent = periods
    blocks = []
    for p in recent:
        win = window_by_keywords(p.text, _LIQUIDITY_KEYWORDS, radius=900, max_chars=2400)
        if not win:
            win = p.text[:2000]
        label = (p.period_end.isoformat() if p.period_end else p.accession)
        blocks.append(f"=== PERIOD {label} ({p.form_type}) ===\n{win}")
    user = (
        "Score management's liquidity / covenant / going-concern framing for each period below "
        "(0 = confident, 100 = going-concern distress). Use only the text in each excerpt.\n\n"
        + "\n\n".join(blocks)
    )
    result = extract_structured(
        system=_TONE_SYSTEM,
        user=user,
        tool_name="score_liquidity_tone",
        tool_description=_TONE_TOOL_DESC,
        input_schema=_TONE_SCHEMA,
        max_tokens=1500,
    )
    if not result or "__error__" in result:
        return
    by_period = {}
    for s in result.get("scores", []):
        try:
            by_period[str(s.get("period"))[:10]] = float(s.get("overall_stress"))
        except (TypeError, ValueError):
            continue
    for p in recent:
        key = p.period_end.isoformat() if p.period_end else None
        if key and key in by_period:
            p.tone = by_period[key]


def build_drift(company, years: int, llm_enabled: bool) -> tuple[list[DriftPoint], list[MdnaPeriod]]:
    periods = build_mdna_series(company, years)
    if not periods:
        return [], []
    _compute_drift(periods)
    _score_tone(periods, llm_enabled)
    points = [
        DriftPoint(
            period_end=p.period_end.isoformat() if p.period_end else None,
            form_type=p.form_type,
            drift_from_prior=p.drift_from_prior,
            liquidity_tone_score=p.tone,
        )
        for p in periods
    ]
    return points, periods
