import React from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts";
import { Card, RISK, Section, chartTooltipStyle, fmtPct, fmtNum, fmtMoney, riskColor } from "../../ui/index.jsx";

function Indicator({ label, value }) {
  return (
    <div className="flex justify-between text-sm py-1 border-b border-ink-700/50">
      <span className="text-slate-400">{label}</span>
      <span className="font-mono text-slate-100">{value}</span>
    </div>
  );
}

export default function MarketPanel({ data }) {
  const m = data.market || {};
  const merton = data.merton || {};
  const cb = data.credit_backdrop || {};
  const ib = data.issuer_bonds || {};
  const pd = merton.pd || {};
  const SIGNAL_COLOR = { "risk-off": RISK.high, neutral: RISK.watch, "risk-on": RISK.ok };
  const pdRows = ["3m", "6m", "12m"].filter((h) => pd[h] != null).map((h) => ({ h, pd: pd[h] * 100 }));

  return (
    <Section flush title="Market & Merton" subtitle="equity-implied default risk and traded levels">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <Card>
          <div className="text-xs text-slate-500 mb-2">Market indicators</div>
          {m.ok ? (
            <>
              <Indicator label="Price" value={m.price != null ? `$${fmtNum(m.price)}` : "—"} />
              <Indicator label="Market cap" value={fmtMoney(m.market_cap)} />
              <Indicator label="Equity vol (ann.)" value={fmtPct(m.equity_vol)} />
              <Indicator label="Drawdown vs 52w high" value={fmtPct(m.drawdown_52w)} />
              <Indicator label="Excess return (1y)" value={fmtPct(m.excess_return_1y)} />
            </>
          ) : (
            <div className="text-sm text-slate-500">Market data unavailable (delisted / no quote).</div>
          )}
        </Card>

        <Card>
          <div className="text-xs text-slate-500 mb-2">Merton PD term structure</div>
          {merton.available ? (
            <>
              <ResponsiveContainer width="100%" height={150}>
                <BarChart data={pdRows} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}>
                  <XAxis dataKey="h" stroke="#64748b" fontSize={12} />
                  <YAxis stroke="#64748b" fontSize={11} unit="%" />
                  <Tooltip
                    contentStyle={chartTooltipStyle}
                    formatter={(v) => [`${v.toFixed(3)}%`, "PD"]}
                  />
                  <Bar dataKey="pd">
                    {pdRows.map((r, i) => (
                      <Cell key={i} fill={riskColor(r.pd * 5)} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
              <div className="text-xs text-slate-400 mt-1">
                DD {fmtNum(merton.value)}σ · asset vol {fmtPct(merton.asset_vol)} ·{" "}
                {merton.converged ? "solver converged" : "fallback (naive assets)"}
              </div>
            </>
          ) : (
            <div className="text-sm text-slate-500">{merton.note || "needs market cap + equity vol"}</div>
          )}
        </Card>

        <Card>
          {ib.enabled && ib.bonds?.length > 0 && (
            <div className="mb-3">
              <div className="text-xs text-slate-500 mb-2">Issuer bonds · last trade (TRACE via finra.org)</div>
              {ib.bonds.map((b) => (
                <div key={b.symbol} className="py-1 border-b border-ink-700/50" title={`${b.issuer} · CUSIP ${b.cusip} · ${b.rating || "unrated"} · scraped ${b.as_of}`}>
                  <div className="flex justify-between text-sm">
                    <span className="text-slate-400">{b.coupon}% {b.maturity?.slice(0, 4)}</span>
                    <span className="font-mono text-slate-100">{fmtNum(b.last_yield)}% yld · ${fmtNum(b.last_price)}</span>
                  </div>
                  <div className="text-[10px] text-slate-600">{b.rating} · traded {b.last_trade}</div>
                </div>
              ))}
            </div>
          )}
          <div className="text-xs text-slate-500 mb-2">Credit backdrop · HY market (TRACE)</div>
          {cb.enabled && cb.hy_breadth != null ? (
            <>
              <div className="text-2xl font-semibold capitalize" style={{ color: SIGNAL_COLOR[cb.signal] }}>
                {cb.signal}
              </div>
              <div className="text-sm text-slate-300 mt-1">
                HY breadth {fmtPct(cb.hy_breadth)} · {cb.hy_advances}↑ / {cb.hy_declines}↓
              </div>
              <div className="text-xs text-slate-500 mt-1">
                {cb.hy_volume_share != null ? `HY ${fmtPct(cb.hy_volume_share)} of volume · ` : ""}as of {cb.as_of}
              </div>
              <div className="text-[10px] text-slate-600 mt-2">
                Market-level (free TRACE aggregate); issuer-level spreads need the bond feed.
              </div>
            </>
          ) : (
            <div className="text-sm text-slate-500">{cb.note || "TRACE not configured"}</div>
          )}
        </Card>
      </div>
    </Section>
  );
}
