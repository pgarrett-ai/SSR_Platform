"""Market data from yfinance: price, equity value/vol, drawdown, excess return.

These feed the market-implied features (Merton inputs, CHS variables, the market-indicators
panel). yfinance is flaky and rate-limited, so every field is optional and the whole thing
degrades to None on failure rather than killing the pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class MarketData:
    price: Optional[float] = None
    shares_outstanding: Optional[float] = None
    market_cap: Optional[float] = None          # equity value E (price * shares)
    equity_vol: Optional[float] = None           # annualized realized vol (daily, ~1y)
    drawdown_52w: Optional[float] = None         # (price / 52w-high - 1), <= 0
    excess_return_1y: Optional[float] = None     # 1y total return minus benchmark
    monthly_returns: Optional[list[float]] = None  # trailing monthly returns (CHS, Phase 2+)
    ok: bool = False


def _annualized_vol(close: np.ndarray) -> Optional[float]:
    if close is None or len(close) < 30:
        return None
    rets = np.diff(np.log(close))
    rets = rets[np.isfinite(rets)]
    if len(rets) < 30:
        return None
    return float(np.std(rets[-252:], ddof=1) * np.sqrt(252))


def get_market_data(ticker: str, index: str = "SPY") -> MarketData:
    """Best-effort market snapshot for an issuer. Returns ok=False if yfinance fails."""
    try:
        import yfinance as yf
    except Exception:
        return MarketData()

    md = MarketData()
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="2y", auto_adjust=True)
        if hist is None or hist.empty:
            return md
        close = hist["Close"].to_numpy(dtype=float)
        close = close[np.isfinite(close)]
        if len(close) == 0:
            return md

        md.price = float(close[-1])
        md.equity_vol = _annualized_vol(close)

        last_252 = close[-252:]
        peak = float(np.max(last_252))
        if peak > 0:
            md.drawdown_52w = float(md.price / peak - 1.0)

        # 1y total return (price already adjusted) vs benchmark.
        if len(close) > 252:
            stock_ret = md.price / float(close[-253]) - 1.0
            try:
                bench = yf.Ticker(index).history(period="2y", auto_adjust=True)["Close"].to_numpy(float)
                bench = bench[np.isfinite(bench)]
                bench_ret = bench[-1] / bench[-253] - 1.0 if len(bench) > 252 else 0.0
            except Exception:
                bench_ret = 0.0
            md.excess_return_1y = float(stock_ret - bench_ret)

        # Shares outstanding -> market cap. info is the flaky part; guard it.
        shares = None
        try:
            shares = tk.info.get("sharesOutstanding")
        except Exception:
            shares = None
        if shares:
            md.shares_outstanding = float(shares)
            md.market_cap = float(shares) * md.price

        # Monthly returns (resample) for later CHS work.
        try:
            m = hist["Close"].resample("ME").last().pct_change().dropna()
            md.monthly_returns = [float(x) for x in m.to_numpy()[-24:]]
        except Exception:
            md.monthly_returns = None

        md.ok = md.price is not None
    except Exception:
        return MarketData()
    return md
