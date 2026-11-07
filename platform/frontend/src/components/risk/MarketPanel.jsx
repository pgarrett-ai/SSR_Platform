import React from "react";
import { Badge, Card, Section, fmtMoney, fmtNum, fmtPct } from "../../ui/index.jsx";

function Indicator({ label, value }) {
  return (
    <div className="flex justify-between text-sm py-1 border-b border-ink-700/50">
      <span className="text-slate-400">{label}</span>
      <span className="font-mono text-slate-100">{value}</span>
    </div>
  );
}

const daysAgo = (iso) => Math.round((Date.now() - new Date(iso)) / 864e5);

export default function MarketPanel({ data }) {
  const m = data.market || {};
  const bonds = data.issuer_bonds?.enabled ? data.issuer_bonds.bonds || [] : [];

  return (
    <Section flush title="Market & bonds" subtitle="equity and last-trade bond levels">
      <Card className="grid gap-x-8 gap-y-4 lg:grid-cols-2">
        <div>
          <div className="text-xs text-slate-500 mb-2">Equity</div>
          {m.ok ? (
            <>
              <Indicator label="Price" value={m.price != null
                ? `$${m.price < 1 ? m.price.toPrecision(2) : fmtNum(m.price)}` : "—"} />
              <Indicator label="Market cap"
                value={fmtMoney(m.market_cap, m.market_cap != null && m.market_cap < 10e6 ? 1 : 0)} />
              <Indicator label="Equity vol (ann.)" value={fmtPct(m.equity_vol)} />
              <Indicator label="Drawdown vs 52w high" value={fmtPct(m.drawdown_52w)} />
              <Indicator label="Excess return (1y)" value={fmtPct(m.excess_return_1y)} />
            </>
          ) : (
            <div className="text-sm text-slate-500">No market quote available.</div>
          )}
        </div>

        {bonds.length > 0 && (
          <div>
            <div className="text-xs text-slate-500 mb-2">Issuer bonds · price in points · yield</div>
            {bonds.map((b) => (
              <div key={b.cusip || b.symbol} className="py-1 border-b border-ink-700/50"
                title={`${b.issuer} · CUSIP ${b.cusip}${b.rating ? ` · ${b.rating}` : ""}`}>
                <div className="flex justify-between text-sm">
                  <span className="text-slate-400">
                    {b.coupon}% {b.maturity?.slice(0, 4)}
                    {b.convertible && <Badge className="ml-1.5">convertible</Badge>}
                  </span>
                  <span className="font-mono text-slate-100">
                    {fmtNum(b.last_price)} · {fmtNum(b.last_yield)}%
                  </span>
                </div>
                {b.last_trade && (
                  <div className="text-[11px] text-slate-400">
                    last trade {b.last_trade} · {daysAgo(b.last_trade)}d ago
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
    </Section>
  );
}
