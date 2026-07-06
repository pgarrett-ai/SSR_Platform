"""Credit features computed from the EDGAR fact series (+ current market data).

Accounting ratios are computed for every fiscal year (so the risk timeline / sparklines are
fully historical). Market-implied features (Merton, vol, drawdown) attach to the latest year
only, since historical shares-outstanding isn't reliably available from yfinance — historical
DD is a Phase-2 refinement.

Every ratio guards against missing/zero denominators and returns None rather than raising,
because real filings have gaps.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from ..edgar import FinancialSeries, YearFacts, raw_value


def _div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _sum(*vals) -> Optional[float]:
    present = [v for v in vals if v is not None]
    return sum(present) if present else None


def total_debt(yf: YearFacts) -> Optional[float]:
    return _sum(raw_value(yf, "lt_debt_noncurrent"),
                raw_value(yf, "lt_debt_current"),
                raw_value(yf, "short_term_debt"))


def ebitda(yf: YearFacts) -> Optional[float]:
    oi, da = raw_value(yf, "operating_income"), raw_value(yf, "d_and_a")
    return None if oi is None else oi + (da or 0.0)


def book_equity(yf: YearFacts) -> Optional[float]:
    se = raw_value(yf, "stockholders_equity")
    if se is not None:
        return se
    a, l = raw_value(yf, "total_assets"), raw_value(yf, "total_liabilities")
    return None if (a is None or l is None) else a - l


def working_capital(yf: YearFacts) -> Optional[float]:
    ca, cl = raw_value(yf, "current_assets"), raw_value(yf, "current_liabilities")
    return None if (ca is None or cl is None) else ca - cl


def fcf(yf: YearFacts) -> Optional[float]:
    ocf, capex = raw_value(yf, "operating_cash_flow"), raw_value(yf, "capex")
    return None if ocf is None else ocf - (capex or 0.0)


def year_features(yf: YearFacts) -> dict:
    """Accounting ratios for one fiscal year (units: ratios are dimensionless, x-multiples)."""
    ta = raw_value(yf, "total_assets")
    tl = raw_value(yf, "total_liabilities")
    ebit = raw_value(yf, "operating_income")
    intex = raw_value(yf, "interest_expense")
    td = total_debt(yf)
    ebd = ebitda(yf)
    capex = raw_value(yf, "capex")
    rev = raw_value(yf, "revenue")
    cash = raw_value(yf, "cash")
    f = {
        "fiscal_year": yf.fiscal_year,
        "leverage": _div(td, ta),                                  # total debt / assets
        "net_debt_to_ebitda": _div(_sum(td, -(cash or 0.0)), ebd),
        "interest_coverage": _div(ebit, intex),                    # EBIT / interest
        "ebitda_capex_coverage": _div((ebd - capex) if (ebd is not None and capex is not None) else None, intex),
        "current_ratio": _div(raw_value(yf, "current_assets"), raw_value(yf, "current_liabilities")),
        "quick_ratio": _div(_sum(raw_value(yf, "current_assets"), -(raw_value(yf, "inventory") or 0.0)),
                            raw_value(yf, "current_liabilities")),
        "cash_ratio": _div(cash, raw_value(yf, "current_liabilities")),
        "roa": _div(ebit, ta),
        "fcf_margin": _div(fcf(yf), rev),
        # Altman Z'' components
        "wc_to_assets": _div(working_capital(yf), ta),
        "re_to_assets": _div(raw_value(yf, "retained_earnings"), ta),
        "ebit_to_assets": _div(ebit, ta),
        "equity_to_liabilities": _div(book_equity(yf), tl),
        "size_log_assets": float(np.log(ta)) if (ta and ta > 0) else None,
        # raw levels (sparklines / tables / CHS inputs)
        "revenue": rev,
        "ebitda": ebd,
        "total_debt": td,
        "total_liabilities": tl,
        "net_income": raw_value(yf, "net_income"),
        "book_equity": book_equity(yf),
        "fcf": fcf(yf),
        "cash": cash,
    }
    return f


class _TTMFact:
    """Fact-shaped wrapper so raw_value/year_features can read TTM floats."""
    __slots__ = ("numeric_value",)

    def __init__(self, v):
        self.numeric_value = v


def quarter_features(qf) -> dict:
    """Same features as year_features, at one quarter end: instant facts at that date +
    trailing-4-quarter (TTM) flows. QuarterFacts.ttm holds plain floats — wrap them
    fact-shaped and reuse year_features unchanged."""
    view = YearFacts(fiscal_year=qf.period_end.year, period_end=qf.period_end,
                     metrics={**{k: _TTMFact(v) for k, v in qf.ttm.items() if v is not None},
                              **qf.metrics})
    f = year_features(view)
    f["label"] = qf.label
    f["period_end"] = qf.period_end.isoformat()
    f["shares_outstanding"] = raw_value(view, "shares_outstanding")
    return f


def year_citations(yf: YearFacts, cik: str) -> dict:
    """Filing provenance for the raw-figures table (Risk page drill-down). Single-fact
    metrics cite their filing verbatim; composites (total debt, EBITDA, FCF) carry the
    component formula as the quote and link to the primary component's filing."""
    from ..edgar.facts import cited_from_fact, cited_metric, fmt_money_millions as money

    out = {}
    for key in ("revenue", "cash", "net_income"):
        cv = cited_metric(yf, key, cik)
        if cv is not None:
            out[key] = cv

    def composite(key, value, parts, op=" + "):
        present = [(p, yf.get(p)) for p in parts if yf.get(p) is not None]
        if value is None or not present:
            return
        formula = op.join(f"{p}={money(getattr(f, 'numeric_value', None))}" for p, f in present)
        cv = cited_from_fact(present[0][1], cik, "instant")
        cv.value, cv.display, cv.derived, cv.formula = value, money(value), True, formula
        cv.citation.quote = f"{formula} [XBRL]"
        out[key] = cv

    composite("total_debt", total_debt(yf),
              ("lt_debt_noncurrent", "lt_debt_current", "short_term_debt"))
    composite("ebitda", ebitda(yf), ("operating_income", "d_and_a"))
    composite("fcf", fcf(yf), ("operating_cash_flow", "capex"), op=" − ")
    return {k: v.model_dump() for k, v in out.items()}


def fcf_margin_slope(timeline: list[dict], window: int = 4) -> Optional[float]:
    """Deterioration rate: OLS slope of FCF margin over the last `window` years."""
    pts = [(t["fiscal_year"], t["fcf_margin"]) for t in timeline if t["fcf_margin"] is not None]
    pts = pts[-window:]
    if len(pts) < 2:
        return None
    x = np.array([p[0] for p in pts], dtype=float)
    y = np.array([p[1] for p in pts], dtype=float)
    return float(np.polyfit(x - x.mean(), y, 1)[0])


def cash_runway_months(series: FinancialSeries) -> Optional[float]:
    """Months of cash at the current burn. inf-like (None) if the firm is FCF-positive."""
    latest = series.latest()
    if latest is None:
        return None
    cash = raw_value(latest, "cash")
    burn = fcf(latest)
    if cash is None or burn is None or burn >= 0:
        return None                       # not burning cash -> runway not the binding constraint
    return cash / (abs(burn) / 12.0)


def build_timeline(series: FinancialSeries) -> list[dict]:
    return [year_features(yf) for yf in series.years]
