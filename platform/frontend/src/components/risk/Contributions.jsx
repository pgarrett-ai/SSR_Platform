import React, { useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, ReferenceLine,
} from "recharts";
import { Section, Card } from "./ui.jsx";

// Higher score is *safer* for Altman; higher logit is *riskier* for CHS.
const SAFER_UP = { "Altman Z''": true };

const LABELS = {
  wc_to_assets: "Working capital / assets",
  re_to_assets: "Retained earnings / assets",
  ebit_to_assets: "EBIT / assets",
  equity_to_liabilities: "Book equity / liabilities",
  NIMTA: "Net income / mkt assets",
  TLMTA: "Liabilities / mkt assets",
  EXRET: "Excess return (1y)",
  SIGMA: "Equity volatility",
  RSIZE: "Relative size",
  CASHMTA: "Cash / mkt assets",
  MB: "Market-to-book",
  PRICE: "Log price",
};

export default function Contributions({ data }) {
  const contrib = data.contributions || {};
  const names = Object.keys(contrib);
  const [active, setActive] = useState(names[0]);
  if (names.length === 0) return null;
  const sel = active && contrib[active] ? active : names[0];

  const saferUp = !!SAFER_UP[sel];
  const rows = Object.entries(contrib[sel])
    .filter(([k]) => k !== "(baseline)")
    .map(([k, v]) => ({ feature: LABELS[k] || k, value: v }))
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value));

  // Green = pushes toward safety, Red = pushes toward distress.
  const colorOf = (v) => {
    const helpful = saferUp ? v > 0 : v < 0;
    return helpful ? "#10b981" : "#f43f5e";
  };

  return (
    <Section
      title="What's driving the score"
      subtitle={`Exact additive contributions — ${saferUp ? "higher bars = safer" : "red bars push toward distress"}`}
      right={
        names.length > 1 && (
          <div className="flex gap-1">
            {names.map((n) => (
              <button
                key={n}
                onClick={() => setActive(n)}
                className={`text-xs px-2 py-1 rounded ${
                  n === sel ? "bg-accent text-white" : "bg-ink-700 text-slate-300"
                }`}
              >
                {n}
              </button>
            ))}
          </div>
        )
      }
    >
      <Card>
        <ResponsiveContainer width="100%" height={Math.max(180, rows.length * 34)}>
          <BarChart data={rows} layout="vertical" margin={{ left: 60, right: 16, top: 4, bottom: 4 }}>
            <XAxis type="number" stroke="#64748b" fontSize={11} />
            <YAxis type="category" dataKey="feature" width={150} stroke="#94a3b8" fontSize={11} />
            <ReferenceLine x={0} stroke="#475569" />
            <Tooltip
              contentStyle={{ background: "#111827", border: "1px solid #263041", borderRadius: 8 }}
              formatter={(v) => [v.toFixed(3), "contribution"]}
            />
            <Bar dataKey="value">
              {rows.map((r, i) => (
                <Cell key={i} fill={colorOf(r.value)} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Card>
    </Section>
  );
}
