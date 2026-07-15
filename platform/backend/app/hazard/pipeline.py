"""Orchestrate one analysis run: ticker/CIK -> the full dashboard payload.

    resolve issuer (EDGAR) -> XBRL fact series -> per-year features
                           -> current market snapshot (yfinance)
                           -> bond spread (TRACE, optional)
                           -> scorers (Altman every year; Merton/CHS latest)
                           -> composite risk score + trend

Returns a plain dict; the FastAPI layer (Phase 3) wraps it in Pydantic schemas.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from .. import edgar
from . import features
from .market import get_market_data
from .score import all_scorers
from .trace import get_credit_backdrop, get_issuer_bonds
from ..core.config import get_settings
from ..core.progress import ProgressLog


def _altman_to_risk(z: Optional[float]) -> Optional[float]:
    """Map Altman Z'' to a 0-100 risk score (Z''=2.6 -> 10 safe, Z''=1.1 -> 90 distress)."""
    if z is None:
        return None
    risk = 90.0 - (z - 1.1) / (2.6 - 1.1) * 80.0
    return float(min(100.0, max(0.0, risk)))


def _leverage_to_risk(lev: float) -> float:
    """Map economic debt/EBITDA to 0-100 risk (2x -> 10 safe, 8x -> 90 distress), clipped."""
    return float(min(100.0, max(0.0, 10.0 + (lev - 2.0) / 6.0 * 80.0)))


def capstack_signals(ticker: str) -> dict:
    """Phase 3 cross-module signals, read from the capstack snapshot cache ONLY — a cache
    miss just means fewer composite inputs; it never triggers a multi-minute live run."""
    from ..core.cache import load_latest_overview

    ov = load_latest_overview(ticker)
    if ov is None:
        return {}
    out = {}
    bridge = ov.economic_debt_bridge
    lev = bridge.economic_leverage.value if bridge and bridge.economic_leverage else None
    if lev is not None and lev > 0:
        out["hidden_leverage"] = {
            "raw": round(float(lev), 2), "unit": "x economic debt / EBITDA",
            "risk": round(_leverage_to_risk(float(lev)), 1),
            "source": "capstack economic-debt bridge (snapshot cache)",
        }
    return out


def _quarterly_risk_timeline(company, sym: str, altman, years: int) -> list[dict]:
    """Per-quarter composite risk: mean of the signals computable at that quarter end
    (Altman from instant + TTM XBRL; Merton PD where shares × price × vol exist) — the
    executive-summary gauge's definition, extended through time."""
    import pandas as pd

    from ..edgar.facts import build_quarterly_series
    from . import merton as merton_mod
    from .market import benchmark_history, pit_market_features, price_history

    try:
        quarters = build_quarterly_series(company, years)
    except Exception:
        return []
    if not quarters:
        return []
    settings = get_settings()
    close = price_history(sym)
    bench = benchmark_history(settings.market_index)
    out = []
    for qf in quarters:
        f = features.quarter_features(qf)
        sc = altman.score(f)
        z = sc.get("value") if sc.get("available") else None
        alt_risk = _altman_to_risk(z)
        merton_risk = None
        shares, debt = f.get("shares_outstanding"), f.get("total_debt")
        if close is not None and shares and debt and debt > 0:
            end = pd.Timestamp(qf.period_end)
            sigma_e = pit_market_features(close, qf.period_end, bench)["equity_vol"]
            w = close.loc[:end]
            price = (float(w.iloc[-1])
                     if len(w) and (end - w.index[-1]).days <= 60 else None)
            if sigma_e and price:
                res = merton_mod.merton(E=shares * price, sigma_E=sigma_e,
                                        D=debt, r=settings.risk_free_rate)
                if res is not None:
                    merton_risk = res.pd_by_horizon[1.0] * 100.0
        parts = {"Merton PD": merton_risk, "Altman": alt_risk}
        avail = {k: v for k, v in parts.items() if v is not None}
        out.append({"fiscal_year": qf.period_end.year, "label": qf.label,
                    "period_end": f["period_end"], "altman_z": z, "altman_risk": alt_risk,
                    "risk": round(float(np.mean(list(avail.values()))), 1) if avail else None,
                    "components": list(avail)})
    return out if any(p["risk"] is not None for p in out) else []


def _trend(risk_series: list[Optional[float]], steps_per_year: int = 1) -> dict:
    """Slope of the last 4 timeline points, annualized (UI label says risk/yr)."""
    pts = [r for r in risk_series if r is not None][-4:]
    if len(pts) < 2:
        return {"direction": "n/a", "slope": None}
    slope = float(np.polyfit(range(len(pts)), pts, 1)[0]) * steps_per_year
    direction = "worsening" if slope > 2 else ("improving" if slope < -2 else "stable")
    return {"direction": direction, "slope": round(slope, 2)}


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
    progress.emit("Fetching credit backdrop + issuer bond quotes…", step="market", pct=55)
    backdrop = get_credit_backdrop()   # market-level credit regime (cached per day)

    # Scores on the latest year.
    progress.emit("Scoring Merton / Altman / trained hazard model…", step="score", pct=65)
    scorers = all_scorers()
    scores = {s.name: s.score(latest, market) for s in scorers}
    contributions = {s.name: s.contributions(latest, market) for s in scorers
                     if s.contributions(latest, market) is not None}

    # Risk timeline: quarterly composite when 10-Q XBRL supports it; annual Altman fallback.
    progress.emit("Building quarterly risk timeline from 10-Q XBRL…", step="timeline", pct=75)
    altman = next(s for s in scorers if s.name == "Altman Z''")
    risk_timeline = _quarterly_risk_timeline(company, sym, altman, years)
    steps_per_year = 4 if risk_timeline else 1
    if not risk_timeline:
        for t in timeline:
            sc = altman.score(t)
            z = sc.get("value") if sc.get("available") else None
            r = _altman_to_risk(z)
            risk_timeline.append({"fiscal_year": t["fiscal_year"],
                                  "label": str(t["fiscal_year"]), "altman_z": z,
                                  "altman_risk": r, "risk": r,
                                  "components": ["Altman"] if r is not None else []})

    # Composite overall risk (0-100): equal-weight blend of available signals, now including
    # the capstack cross-module signal (hidden leverage) when a snapshot exists.
    signals, composite_of = [], []
    merton = scores.get("Merton DD", {})
    if merton.get("available") and merton.get("pd", {}).get("12m") is not None:
        signals.append(merton["pd"]["12m"] * 100.0)
        composite_of.append("Merton PD")
    if risk_timeline and risk_timeline[-1]["altman_risk"] is not None:
        signals.append(risk_timeline[-1]["altman_risk"])
        composite_of.append("Altman")
    cross = capstack_signals(sym) or capstack_signals(ticker)
    if "hidden_leverage" in cross:
        signals.append(cross["hidden_leverage"]["risk"])
        composite_of.append("hidden leverage")
    overall_risk = round(float(np.mean(signals)), 1) if signals else None
    # The gauge and the timeline's final point are the same blend by construction —
    # the last point simply gets the full signal set (live Merton + cross-module signals).
    if risk_timeline and overall_risk is not None:
        risk_timeline[-1]["risk"] = overall_risk
        risk_timeline[-1]["components"] = composite_of

    progress.emit("Assembling payload (filings timeline, bonds, scores)…", step="assemble", pct=90)
    return {
        "issuer": {"ticker": sym, "name": getattr(company, "name", None),
                   "cik": str(company.cik)},
        "executive_summary": {
            "overall_risk": overall_risk,
            "composite_of": composite_of,
            "distress_pd": merton.get("pd") if merton.get("available") else None,
            "distance_to_default": merton.get("value") if merton.get("available") else None,
            "trend": _trend([r["risk"] for r in risk_timeline], steps_per_year),
        },
        "cross_signals": cross,
        "scores": scores,
        "contributions": contributions,
        "risk_timeline": risk_timeline,
        "features_timeline": timeline,
        "market": market.__dict__,
        "credit_backdrop": backdrop.__dict__,
        "issuer_bonds": get_issuer_bonds(sym),

        "filings": edgar.timeline_filings(company, years)[:60],
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
    print(f"  overall risk: {es['overall_risk']}  trend: {es['trend']['direction']}")
    print(f"  distance-to-default: {es['distance_to_default']}  PD: {es['distress_pd']}")
    print(f"  fiscal years: {[r['fiscal_year'] for r in result['risk_timeline']]}")
    for name, sc in result["scores"].items():
        print(f"  {name}: {sc}")
    cb = result["credit_backdrop"]
    print(f"  market ok: {result['market'].get('ok')}  "
          f"credit backdrop: {cb.get('signal') or cb.get('note')}"
          + (f" (HY breadth {cb['hy_breadth']:.2f} as of {cb['as_of']})" if cb.get('hy_breadth') is not None else ""))
    if "--json" in sys.argv:
        print(json.dumps(result, default=str, indent=2))
