"""Orchestrate one Default Risk run: ticker/CIK -> the dashboard payload.

    resolve issuer (EDGAR) -> XBRL fact series -> per-year features
                           -> current market snapshot (yfinance)
                           -> issuer bond marks (local marks file)
                           -> scorers on the latest year (Altman, Merton, trained model)
                           -> Altman Z'' history per quarter (annual fallback)

Returns a plain dict.
"""
from __future__ import annotations

from typing import Optional

from .. import edgar
from . import features
from .market import get_market_data
from .score import all_scorers
from .trace import get_issuer_bonds
from ..core.config import get_settings
from ..core.progress import ProgressLog


def _quarterly_risk_timeline(company, altman, years: int) -> list[dict]:
    """Altman Z'' at each quarter end, from instant + TTM XBRL; [] when no quarter scores."""
    from ..edgar.facts import build_quarterly_series

    try:
        quarters = build_quarterly_series(company, years)
    except Exception:
        return []
    out = []
    for qf in quarters or []:
        f = features.quarter_features(qf)
        sc = altman.score(f)
        out.append({"fiscal_year": qf.period_end.year, "label": qf.label,
                    "period_end": f["period_end"],
                    "altman_z": sc.get("value") if sc.get("available") else None})
    return out if any(p["altman_z"] is not None for p in out) else []


def analyze(ticker: str, years: int = 10, progress: Optional[ProgressLog] = None) -> dict:
    if not ticker or not ticker.strip():
        raise ValueError("ticker/CIK is required")
    years = max(1, min(int(years), 10))
    progress = progress or ProgressLog()

    progress.emit(f"Resolving ticker {ticker} → CIK…", step="resolve", pct=5)
    company = edgar.resolve_company(ticker)          # raises TickerNotFoundError
    progress.emit(f"Pulling XBRL financial series ({years}y) from EDGAR…", step="xbrl", pct=15)
    series = edgar.build_financial_series(company, years)
    if not series.years:
        raise ValueError(f"No XBRL financial facts found for {ticker}.")

    timeline = features.build_timeline(series)
    for yf, row in zip(series.years, timeline):   # filing provenance for the raw-figures table
        row["cited"] = features.year_citations(yf, str(company.cik))
    latest = timeline[-1]
    sym = edgar.current_ticker(company) or ticker.upper()
    progress.emit(f"Built {len(timeline)} fiscal-year feature rows.", step="xbrl", pct=35)

    progress.emit("Fetching market data (equity price, vol, index)…", step="market", pct=45)
    market = get_market_data(sym, index=get_settings().market_index)

    # Scores on the latest year.
    progress.emit("Scoring Merton / Altman / trained hazard model…", step="score", pct=65)
    scorers = all_scorers()
    scores = {s.name: s.score(latest, market) for s in scorers}

    # Altman Z'' history: quarterly when 10-Q XBRL supports it, else one point per fiscal year.
    progress.emit("Building quarterly Altman Z'' history from 10-Q XBRL…", step="timeline", pct=75)
    altman = next(s for s in scorers if s.name == "Altman Z''")
    risk_timeline = _quarterly_risk_timeline(company, altman, years)
    if not risk_timeline:
        for t in timeline:
            sc = altman.score(t)
            risk_timeline.append({"fiscal_year": t["fiscal_year"],
                                  "label": str(t["fiscal_year"]),
                                  "altman_z": sc.get("value") if sc.get("available") else None})

    merton = scores.get("Merton DD", {})
    progress.emit("Assembling payload (bonds, scores)…", step="assemble", pct=90)
    return {
        "issuer": {"ticker": sym, "name": getattr(company, "name", None),
                   "cik": str(company.cik)},
        "executive_summary": {
            "distress_pd": merton.get("pd") if merton.get("available") else None,
            "distance_to_default": merton.get("value") if merton.get("available") else None,
        },
        "scores": scores,
        "risk_timeline": risk_timeline,
        "features_timeline": timeline,
        "market": market.__dict__,
        "issuer_bonds": get_issuer_bonds(sym),
        "merton": scores.get("Merton DD"),
        "params": {"years": years},
    }


if __name__ == "__main__":
    import json
    import sys

    tk = sys.argv[1] if len(sys.argv) > 1 else "AAL"
    yrs = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    result = analyze(tk, yrs)
    es = result["executive_summary"]
    print(f"\n{result['issuer']['name']} ({result['issuer']['ticker']}, "
          f"CIK {result['issuer']['cik']})")
    print(f"  distance-to-default: {es['distance_to_default']}  PD: {es['distress_pd']}")
    print(f"  fiscal years: {[r['fiscal_year'] for r in result['risk_timeline']]}")
    for name, sc in result["scores"].items():
        print(f"  {name}: {sc}")
    print(f"  market ok: {result['market'].get('ok')}")
    if "--json" in sys.argv:
        print(json.dumps(result, default=str, indent=2))
