import React, { useState } from "react";
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { fetchCapacity } from "../api.js";
import { useAsync } from "../cache.js";
import { INK, LINE_COLORS, Loading, Th, chartTooltipStyle, fmt } from "../ui/index.jsx";

// Credit-capacity card (Moyer ch. 6): can this structure repay itself from internal
// funds? Cash-sweep repayment %, the leverage×growth heatmap with the issuer's own cell
// ringed, and the cycle-severity slider. Repayment collapses between 3x and 5x.

const heatColor = (v) =>
  v == null ? "transparent" :
  v >= 90 ? "rgba(52,211,153,0.35)" :
  v >= 60 ? "rgba(52,211,153,0.18)" :
  v >= 30 ? "rgba(251,191,36,0.18)" :
  "rgba(251,113,133,0.20)";

export default function CapacityCard({ ticker, years }) {
  const { data, loading, error } = useAsync(
    `capacity:${ticker}:${years}`, () => fetchCapacity(ticker, years), [ticker, years]);
  const [sevIdx, setSevIdx] = useState(2);   // severity slices: 0.5 .. 1.75; default 1.0

  if (loading) return <Loading />;
  if (error) return <div className="text-xs text-rose-300">{error}</div>;
  if (!data) return null;
  if (!data.available) return <div className="text-xs text-slate-500">{data.note}</div>;

  const { inputs, base_sweep, heatmap, severity } = data;
  const slice = severity[sevIdx];
  const pathData = base_sweep.rows.map((r, i) => ({
    year: `Y${i + 1}`, leverage: r.leverage, coverage: r.coverage,
  }));

  // nearest heatmap cell to the issuer's actual leverage (growth col: 2%)
  const actualLevIdx = heatmap.leverage.reduce(
    (best, lv, i) => Math.abs(lv - inputs.leverage) < Math.abs(heatmap.leverage[best] - inputs.leverage) ? i : best, 0);

  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-slate-400">
        <span>debt <span className="font-mono text-slate-200">{fmt(inputs.debt, 0)} $mm</span></span>
        <span>EBITDA <span className="font-mono text-slate-200">{fmt(inputs.ebitda, 0)} $mm</span></span>
        <span title={inputs.capex_note}>capex <span className="font-mono text-slate-200">{fmt(inputs.capex, 0)} $mm</span> ({inputs.capex_pct}% of EBITDA)</span>
        <span title={inputs.rate_note}>w.a. rate <span className="font-mono text-slate-200">{(100 * inputs.rate).toFixed(2)}%</span></span>
        <span>5-yr self-repayment at 2% growth: <span className="font-mono text-slate-100">{fmt(base_sweep.pct_retired, 0)}%</span></span>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <div>
          <div className="mb-1 text-xs text-slate-500">
            % of debt retired in 5 years — leverage × growth (issuer's cell ringed)
          </div>
          <div className="inline-grid gap-0.5 text-[11px]"
            style={{ gridTemplateColumns: `auto repeat(${heatmap.growth.length}, 3.2rem)` }}>
            <div />
            {heatmap.growth.map((g) => (
              <div key={g} className="text-center text-slate-500">{Math.round(100 * g)}%</div>
            ))}
            {heatmap.leverage.map((lv, i) => (
              <React.Fragment key={lv}>
                <div className="pr-2 text-right font-mono text-slate-500">{lv.toFixed(1)}x</div>
                {heatmap.growth.map((g, j) => (
                  <div key={j}
                    className={`rounded px-1 py-0.5 text-center font-mono text-slate-200 ${i === actualLevIdx ? "ring-1 ring-accent" : ""}`}
                    style={{ background: heatColor(heatmap.pct_retired[i][j]) }}>
                    {heatmap.pct_retired[i][j] == null ? "—" : Math.round(heatmap.pct_retired[i][j])}
                  </div>
                ))}
              </React.Fragment>
            ))}
          </div>
        </div>

        <div>
          <div className="mb-1 flex items-center justify-between text-xs text-slate-500">
            <span>cycle stress — severity {slice.severity}× of (+5, −20, −10, +10, +10)%</span>
            <input type="range" min={0} max={severity.length - 1} value={sevIdx}
              onChange={(e) => setSevIdx(Number(e.target.value))} className="w-32 accent-accent" />
          </div>
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="border-b border-ink-600">
                <Th>Yr</Th><Th right>EBITDA</Th><Th right>Interest</Th><Th right>Sweep</Th>
                <Th right>Debt end</Th><Th right>Lev</Th><Th />
              </tr>
            </thead>
            <tbody>
              {slice.rows.map((r, i) => (
                <tr key={i} className="border-b border-ink-700/60 font-mono text-slate-300">
                  <td className="px-2 py-1">{i + 1}</td>
                  <td className="px-2 py-1 text-right">{fmt(r.ebitda, 0)}</td>
                  <td className="px-2 py-1 text-right">{fmt(r.interest, 0)}</td>
                  <td className="px-2 py-1 text-right">{fmt(r.available, 0)}</td>
                  <td className="px-2 py-1 text-right">{fmt(r.debt_end, 0)}</td>
                  <td className="px-2 py-1 text-right">{r.leverage == null ? "n.m." : `${r.leverage.toFixed(1)}x`}</td>
                  <td className="px-2 py-1 font-sans">
                    {(slice.year_flags[i] || []).map((f) => (
                      <span key={f} className="mr-1 rounded bg-rose-500/20 px-1.5 py-0.5 text-[9px] uppercase text-rose-200"
                        title={f === "wall_breach" ? "maturity-wall face due exceeds cumulative sweep capacity (no amortization schedules extracted — wall substitute)" : "leverage rose despite amortization — the denominator fell faster"}>
                        {f.replace(/_/g, " ")}
                      </span>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-1 font-mono text-[11px] text-slate-500">
            retired over the cycle: {slice.pct_retired == null ? "—" : `${Math.round(slice.pct_retired)}%`}
          </div>
        </div>
      </div>

      <div className="mt-4">
        <div className="mb-1 text-xs text-slate-500">base-case paths (2% growth)</div>
        <ResponsiveContainer width="100%" height={140}>
          <LineChart data={pathData} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
            <CartesianGrid stroke={INK[600]} strokeDasharray="3 3" />
            <XAxis dataKey="year" tick={{ fill: "#94a3b8", fontSize: 10 }} />
            <YAxis tick={{ fill: "#94a3b8", fontSize: 10 }} />
            <Tooltip contentStyle={chartTooltipStyle} />
            <Line dataKey="leverage" name="Debt/EBITDA" stroke={LINE_COLORS[0]} dot={false} unit="x" />
            <Line dataKey="coverage" name="EBITDA/interest" stroke={LINE_COLORS[1]} dot={false} unit="x" />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-2 text-[11px] text-slate-600">{data.derivation}</div>
    </div>
  );
}
