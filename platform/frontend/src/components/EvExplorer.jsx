import React, { useMemo, useState } from "react";
import {
  CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { exploreRecovery } from "../api.js";
import { Button, INK, LINE_COLORS, Section, Th, chartTooltipStyle, fmt } from "../ui/index.jsx";

// Deterministic EV explorer (Moyer): who is in the money at EV = X. One waterfall pass
// over an EV grid; the slider, breakpoints, and inverse solver all read the grid
// client-side. Works at negative EBITDA (raw-EV axis, multiples omitted).

export default function EvExplorer({ ticker, years, structure, baseEbitda, accrualYears }) {
  const [data, setData] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);
  const [idx, setIdx] = useState(120);          // slider index into ev_grid
  const [invTranche, setInvTranche] = useState(null);
  const [invPrice, setInvPrice] = useState(60);

  async function run() {
    setRunning(true);
    setError(null);
    try {
      const d = await exploreRecovery(ticker, {
        structure,
        sim: { base_ebitda: baseEbitda, accrual_years: accrualYears },
      }, years);
      setData(d);
      setIdx(Math.floor((d.ev_grid?.length || 241) / 2));
      setInvTranche(d.tranches?.[0]?.tranche || null);
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  }

  const ev = data?.ev_grid?.[idx];
  const mult = data?.multiple_grid?.[idx];
  const atSlider = useMemo(() => {
    if (!data) return [];
    return data.tranches.map((t) => ({
      tranche: t.tranche,
      pct: t.recovery_pct[idx],
      enters: t.ev_enters,
      covered: t.ev_covered,
    }));
  }, [data, idx]);
  const fulcrumAt = atSlider.find((t) => t.pct > 0.05 && t.pct < 99.9)?.tranche;

  // Inverse solver: tranche price % of claim -> EV (monotone curve, linear interpolation).
  const implied = useMemo(() => {
    if (!data || !invTranche) return null;
    const t = data.tranches.find((x) => x.tranche === invTranche);
    if (!t) return null;
    const curve = t.recovery_pct;
    const i = curve.findIndex((p) => p >= invPrice);
    if (i < 0) return null;
    let evx = data.ev_grid[i];
    if (i > 0 && curve[i] > curve[i - 1]) {
      const f = (invPrice - curve[i - 1]) / (curve[i] - curve[i - 1]);
      evx = data.ev_grid[i - 1] + f * (data.ev_grid[i] - data.ev_grid[i - 1]);
    }
    return { ev: evx, multiple: data.ebitda > 0 ? evx / data.ebitda : null };
  }, [data, invTranche, invPrice]);

  const covChart = useMemo(() => {
    if (!data?.coverage) return null;
    return data.coverage.multiple.map((m, i) => ({
      m,
      total: data.coverage.total[i],
      ...(data.coverage.senior ? { senior: data.coverage.senior[i] } : {}),
      ...(data.coverage.junior ? { junior: data.coverage.junior[i] } : {}),
    }));
  }, [data]);

  return (
    <Section
      title="EV explorer"
      subtitle="deterministic: who is in the money at EV = X · breakpoints per class · market-implied EV (Moyer)"
    >
      {!data && (
        <div className="flex items-center gap-3">
          <Button variant="primary" onClick={run} disabled={running || !structure}>
            {running ? "Computing…" : "Run explorer"}
          </Button>
          <span className="text-xs text-slate-500">
            one deterministic waterfall pass over an EV grid — no simulation
          </span>
          {error && <span className="text-xs text-rose-300">{error}</span>}
        </div>
      )}

      {data?.not_repriced && (
        <div className="mb-4 rounded-xl border border-rose-500/50 bg-rose-500/10 p-3 text-sm text-rose-100">
          <span className="font-bold">Market has not repriced:</span> {data.not_repriced_note}
        </div>
      )}

      {data && (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-slate-400">
            <label className="flex items-center gap-3">
              <span className="text-[10px] uppercase tracking-wide text-slate-500">EV $mm</span>
              <input type="range" min={0} max={data.ev_grid.length - 1} value={idx}
                onChange={(e) => setIdx(Number(e.target.value))} className="w-64 accent-accent" />
              <span className="font-mono text-slate-100">{fmt(ev, 0)}</span>
              {mult != null && <span className="font-mono text-slate-400">= {mult.toFixed(1)}x EBITDA</span>}
            </label>
            {fulcrumAt && <span>fulcrum at this EV: <span className="font-mono text-rose-300">{fulcrumAt}</span></span>}
            <Button onClick={run} disabled={running}>{running ? "…" : "Recompute"}</Button>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-xs">
              <thead>
                <tr className="border-b border-ink-600">
                  <Th>Tranche</Th>
                  <Th right>Recovery % @ EV</Th>
                  <Th right title="EV at which the class first sees value">Enters $mm</Th>
                  <Th right title="EV at which the class is paid its full claim">Covered $mm</Th>
                  {data.ebitda > 0 && <Th right>Covered x</Th>}
                </tr>
              </thead>
              <tbody>
                {atSlider.map((t) => (
                  <tr key={t.tranche}
                    className={`border-b border-ink-700/60 font-mono ${t.tranche === fulcrumAt ? "bg-rose-900/30 text-rose-100" : "text-slate-300"}`}>
                    <td className="px-2 py-1.5 font-sans">{t.tranche}</td>
                    <td className="px-2 py-1.5 text-right">{fmt(t.pct)}</td>
                    <td className="px-2 py-1.5 text-right">{t.enters == null ? "—" : fmt(t.enters, 0)}</td>
                    <td className="px-2 py-1.5 text-right">{t.covered == null ? "—" : fmt(t.covered, 0)}</td>
                    {data.ebitda > 0 && (
                      <td className="px-2 py-1.5 text-right">
                        {t.covered == null ? "—" : `${(t.covered / data.ebitda).toFixed(1)}x`}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mt-4 flex flex-wrap items-end gap-3 text-xs">
            <span className="text-[10px] uppercase tracking-wide text-slate-500">
              Inverse: what EV do the bonds price?
            </span>
            <select value={invTranche || ""} onChange={(e) => setInvTranche(e.target.value)}
              className="max-w-[240px] rounded-md border border-ink-600 bg-ink-800 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-accent">
              {data.tranches.map((t) => (
                <option key={t.tranche} value={t.tranche}>{t.tranche}</option>
              ))}
            </select>
            <input type="number" step={1} value={invPrice}
              onChange={(e) => setInvPrice(Number(e.target.value))}
              className="w-24 rounded-md border border-ink-600 bg-ink-800 px-2 py-1.5 font-mono text-xs text-slate-100 outline-none focus:border-accent"
              title="price as % of allowed claim" />
            {implied ? (
              <span className="text-slate-300">
                → implied EV <span className="font-mono text-slate-100">{fmt(implied.ev, 0)} $mm</span>
                {implied.multiple != null && (
                  <span> = <span className="font-mono text-slate-100">{implied.multiple.toFixed(1)}x</span> EBITDA</span>
                )}
              </span>
            ) : (
              <span className="text-slate-600">price not reachable on the curve</span>
            )}
          </div>

          {covChart && (
            <div className="mt-5">
              <div className="mb-1 text-xs text-slate-500" title={data.coverage_note}>
                Asset coverage vs EV multiple (Moyer ch. 6) — breakevens:{" "}
                {Object.entries(data.breakeven_multiples).map(([k, v]) => `${k} ${v}x`).join(" · ")}
              </div>
              <ResponsiveContainer width="100%" height={200}>
                <LineChart data={covChart} margin={{ top: 4, right: 8, bottom: 4, left: 8 }}>
                  <CartesianGrid stroke={INK[600]} strokeDasharray="3 3" />
                  <XAxis dataKey="m" tick={{ fill: "#94a3b8", fontSize: 10 }} unit="x" />
                  <YAxis tick={{ fill: "#94a3b8", fontSize: 10 }} />
                  <Tooltip contentStyle={chartTooltipStyle} formatter={(v) => `${fmt(v, 2)}x coverage`}
                    labelFormatter={(m) => `EV = ${m}x EBITDA`} />
                  <ReferenceLine y={1.0} stroke="#fb7185" strokeDasharray="4 4" />
                  {["total", "senior", "junior"].filter((k) => covChart[0][k] != null).map((k, i) => (
                    <Line key={k} dataKey={k} stroke={LINE_COLORS[i % LINE_COLORS.length]} dot={false} />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}
    </Section>
  );
}
