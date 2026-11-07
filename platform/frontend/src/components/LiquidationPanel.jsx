import React, { useEffect, useState } from "react";
import { liquidateRecovery } from "../api.js";
import { Button, ErrorCard, Section, Th, fmt, fmtMoney } from "../ui/index.jsx";

// Asset-based liquidation waterfall (Moyer, Distressed Debt Analysis, ch. 5 and 8): book
// values × advance rates, net of
// estate costs, distributed by absolute priority. Recovery shows it in place of the
// going-concern answer when EBITDA ≤ 0. Recompute keeps the simulated run's stack and
// accrual, so its claims match the run it replaces.

const CATS = ["cash", "accounts_receivable", "inventory", "ppe", "intangibles", "other"];

function RateCell({ value, onChange }) {
  return (
    <input type="number" step={0.05} min={0} max={1} value={value ?? ""}
      onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value))}
      className="w-20 rounded border border-ink-600 bg-ink-800 px-2 py-1 text-right font-mono text-xs text-slate-100 outline-none focus:border-accent" />
  );
}

export default function LiquidationPanel({ ticker, years, initial }) {
  const [data, setData] = useState(initial || null);
  const [rates, setRates] = useState(null);
  const [adminPct, setAdminPct] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => { setData(initial || null); }, [initial]);

  const scenario = data?.scenario;

  useEffect(() => {
    if (scenario && rates == null) {
      setRates(Object.fromEntries(scenario.lines.map((l) => [l.key, l.rate])));
      setAdminPct(scenario.admin_pct);
    }
  }, [scenario, rates]);

  async function rerun(preset) {
    setRunning(true);
    setError(null);
    try {
      const body = { structure: initial.structure, accrual_years: initial.accrual_years || 0 };
      if (preset) {
        body.rates = data?.presets?.[preset];
        body.admin_pct = preset === "fire_sale" ? data?.presets?.admin_ch7 : data?.presets?.admin_ch11;
      } else {
        body.rates = rates;
        body.admin_pct = adminPct;
      }
      if (scenario) {
        body.assets = Object.fromEntries(scenario.lines.map((l) => [l.key, l.book]));
      }
      const d = await liquidateRecovery(ticker, body, years);
      setData(d);
      if (d.scenario) {
        setRates(Object.fromEntries(d.scenario.lines.map((l) => [l.key, l.rate])));
        setAdminPct(d.scenario.admin_pct);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  }

  if (!data) return null;
  if (data.available === false) {
    return (
      <Section title="Liquidation waterfall" subtitle="asset-based recovery — EBITDA ≤ 0">
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-3 text-[13px] text-amber-200/90">
          ⚠ {data.detail || data.note}
        </div>
      </Section>
    );
  }

  const pair = data.ch11_vs_ch7;

  return (
    <Section
      title="Liquidation waterfall"
      subtitle={initial?.note || data.note}
    >
      {error && <ErrorCard className="mb-4">{error}</ErrorCard>}

      <div className="mb-4 flex flex-wrap items-end gap-3 text-xs">
        <Button onClick={() => rerun("orderly")} disabled={running}>ch. 11 orderly preset</Button>
        <Button onClick={() => rerun("fire_sale")} disabled={running}>ch. 7 fire-sale preset</Button>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] uppercase tracking-wide text-slate-400">Estate costs % of proceeds</span>
          <input type="number" step={0.01} min={0} max={0.4} value={adminPct ?? ""}
            onChange={(e) => setAdminPct(Number(e.target.value))}
            className="w-24 rounded-md border border-ink-600 bg-ink-800 px-2 py-1.5 font-mono text-xs text-slate-100 outline-none focus:border-accent" />
        </label>
        <Button variant="primary" onClick={() => rerun(null)} disabled={running}>
          {running ? "Computing…" : "Recompute"}
        </Button>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <div>
          <div className="mb-1 text-xs text-slate-500">Proceeds build — book × advance rate</div>
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="border-b border-ink-600">
                <Th>Asset</Th><Th right>Book $M</Th><Th right>Rate</Th><Th right>Proceeds $M</Th>
              </tr>
            </thead>
            <tbody>
              {scenario.lines.map((l) => (
                <tr key={l.key} className="border-b border-ink-700/60 text-slate-300">
                  <td className="px-2 py-1.5" title={l.formula}>{l.label}</td>
                  <td className="px-2 py-1.5 text-right font-mono">{fmt(l.book, 0)}</td>
                  <td className="px-2 py-1.5 text-right">
                    <RateCell value={rates?.[l.key]} onChange={(v) => setRates({ ...rates, [l.key]: v })} />
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono">{fmt(l.proceeds, 0)}</td>
                </tr>
              ))}
              <tr className="text-slate-100">
                <td className="px-2 py-1.5 font-semibold">Gross → net of {Math.round(100 * scenario.admin_pct)}% costs</td>
                <td />
                <td className="px-2 py-1.5 text-right font-mono">{fmt(scenario.gross_proceeds, 0)}</td>
                <td className="px-2 py-1.5 text-right font-mono text-slate-100">{fmt(scenario.net_proceeds, 0)}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div>
          <div className="mb-1 text-xs text-slate-500">Distribution — absolute priority on net proceeds</div>
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="border-b border-ink-600">
                <Th>Tranche</Th><Th right>Claim $M</Th><Th right>Recovery $M</Th><Th right>% of claim</Th>
              </tr>
            </thead>
            <tbody>
              {scenario.tranches.map((r) => (
                <tr key={r.tranche}
                  className={`border-b border-ink-700/60 font-mono ${r.is_fulcrum ? "bg-rose-900/40 text-rose-100" : "text-slate-300"}`}>
                  <td className="px-2 py-1.5 font-sans">
                    {r.tranche}
                    {r.is_fulcrum && <span className="ml-2 rounded bg-rose-500/30 px-1.5 py-0.5 text-[11px] uppercase">fulcrum</span>}
                  </td>
                  <td className="px-2 py-1.5 text-right">{fmt(r.claim, 0)}</td>
                  <td className="px-2 py-1.5 text-right">{fmt(r.recovery, 0)}</td>
                  <td className="px-2 py-1.5 text-right">{r.recovery_pct == null ? "—" : fmt(r.recovery_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {pair && (
            <div className="mt-3 text-xs text-slate-400">
              <span className="text-[11px] uppercase tracking-wide text-slate-400">ch. 11 orderly vs ch. 7 fire-sale (net proceeds): </span>
              <span className="font-mono text-slate-200">{fmtMoney(pair.ch11_orderly.net_proceeds * 1e6)}</span>
              <span className="mx-1 text-slate-400">vs</span>
              <span className="font-mono text-rose-300">{fmtMoney(pair.ch7_fire_sale.net_proceeds * 1e6)}</span>
              <span className="ml-2 text-slate-500">— the going-concern vs shutdown gap</span>
            </div>
          )}
        </div>
      </div>
    </Section>
  );
}
