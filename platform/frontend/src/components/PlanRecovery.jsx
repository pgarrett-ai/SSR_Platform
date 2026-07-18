import React, { useEffect, useState } from "react";
import { planRecovery } from "../api.js";
import CitedNumber from "./CitedNumber.jsx";
import { Badge, Button, Section, Th, fmt } from "../ui/index.jsx";

// Plan-of-reorganization recovery & ROI (Moyer ch. 12-13): the analyst types what each
// class receives (cash + discounted new debt + equity % + warrants/rights); the server
// values the package, divides by the allowed claim, and annualizes vs the market quote,
// with a delta vs the absolute-priority recovery at the same reorg EV. Clones the
// ExchangeAnalyzer typed-terms → POST → client-read pattern.
// PR3: lift reorgEv/reorgDebt to shared RecoveryPage state so F5/F6 use the same figure.

const numCls =
  "w-20 rounded-md border border-ink-600 bg-ink-800 px-2 py-1 font-mono text-xs text-slate-100 outline-none focus:border-accent";

function TermField({ label, title, children }) {
  return (
    <label className="flex flex-col gap-1" title={title}>
      <span className="text-[10px] uppercase tracking-wide text-slate-500">{label}</span>
      {children}
    </label>
  );
}

const EMPTY = { cash: 0, new_debt_face: 0, new_debt_mkt_pct: 70, new_equity_pct: 0,
                warrant_value: 0, rights_shares: 0, rights_strike: 0 };

export default function PlanRecovery({ ticker, years, structure, baseEbitda, accrualYears,
  petitionDate, reorgEv, setReorgEv, reorgDebt, setReorgDebt, reorgShares, setReorgShares }) {
  // reorgEv/reorgDebt/reorgShares are lifted to RecoveryPage state (decision #4) so the
  // post-reorg technicals (F5) and tax card (F6) read the identical reorg-equity figure.
  const tranches = structure?.tranches || [];
  const [duration, setDuration] = useState("");
  const [plan, setPlan] = useState({});           // tranche name -> consideration fields
  const [data, setData] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);

  // seed one empty row per tranche when the structure loads/changes
  useEffect(() => {
    setPlan(Object.fromEntries(tranches.map((t) => [t.name, { ...EMPTY }])));
    setData(null);
  }, [structure]);   // eslint-disable-line react-hooks/exhaustive-deps

  function patch(name, field, value) {
    setPlan((p) => ({ ...p, [name]: { ...(p[name] || EMPTY), [field]: value } }));
  }

  async function run() {
    setRunning(true);
    setError(null);
    try {
      const rows = tranches.map((t) => {
        const c = plan[t.name] || EMPTY;
        return {
          tranche: t.name,
          cash: Number(c.cash) || 0,
          new_debt_face: Number(c.new_debt_face) || 0,
          // blank -> null so the backend "haircut required when new debt > 0" 400 surfaces,
          // rather than silently valuing new debt at 0
          new_debt_haircut: c.new_debt_mkt_pct === "" ? null : Number(c.new_debt_mkt_pct) / 100,
          new_equity_pct: Number(c.new_equity_pct) || 0,
          warrant_value: Number(c.warrant_value) || 0,
          rights_shares: Number(c.rights_shares) || 0,
          rights_strike: Number(c.rights_strike) || 0,
        };
      });
      const d = await planRecovery(ticker, {
        structure,
        sim: { base_ebitda: baseEbitda, accrual_years: accrualYears },
        petition_date: petitionDate,
        reorg_ev: Number(reorgEv) || 0,
        reorg_debt: Number(reorgDebt) || 0,
        reorg_shares: reorgShares === "" ? null : Number(reorgShares),
        duration_years: duration === "" ? null : Number(duration),
        plan: rows,
      }, years);
      setData(d);
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  }

  const cols = [
    ["cash", "Cash $mm", "cash consideration, valued at face"],
    ["new_debt_face", "New debt $mm", "face of new debt received"],
    ["new_debt_mkt_pct", "New debt mkt %", "market value as % of new-debt face (post-reorg debt trades at a discount)"],
    ["new_equity_pct", "Equity %", "this class's % of reorg equity value (plan EV − post-reorg debt)"],
    ["warrant_value", "Warrant $mm", "analyst estimate of warrant value (no option model in v1)"],
    ["rights_shares", "Rights sh (mm)", "subscription-rights shares this class may buy"],
    ["rights_strike", "Rights strike $", "per-share subscription price; intrinsic value = max(0, per-share equity − strike)"],
  ];

  return (
    <Section
      title="Plan recovery & ROI"
      subtitle="value the plan package per class → recovery % of claim → annualized ROI vs market (Moyer ch. 12-13)"
    >
      <div className="mb-4 flex flex-wrap items-end gap-x-5 gap-y-3">
        <TermField label="Reorg EV $mm" title="plan enterprise value">
          <input type="number" step={50} value={reorgEv} onChange={(e) => setReorgEv(e.target.value)} className={numCls} />
        </TermField>
        <TermField label="Post-reorg debt $mm" title="debt remaining after the plan — reorg equity = EV − this">
          <input type="number" step={50} value={reorgDebt} onChange={(e) => setReorgDebt(e.target.value)} className={numCls} />
        </TermField>
        <TermField label="Reorg shares (mm)" title="post-reorg share count — needed to value subscription rights">
          <input type="number" step={1} value={reorgShares} onChange={(e) => setReorgShares(e.target.value)} className={numCls} />
        </TermField>
        <TermField label="Duration (yrs)" title="time to emergence for the ROI annualization; defaults to the ~14-month ch.12 benchmark">
          <input type="number" step={0.25} value={duration} onChange={(e) => setDuration(e.target.value)} className={numCls}
            placeholder="1.17" />
        </TermField>
        <div className="pb-0.5">
          <Button variant="primary" onClick={run} disabled={running || !tranches.length}>
            {running ? "Computing…" : "Value plan"}
          </Button>
        </div>
        {error && <span className="pb-2 text-xs text-rose-300">{error}</span>}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-ink-600">
              <Th>Tranche</Th>
              {cols.map(([k, label, title]) => <Th key={k} right title={title}>{label}</Th>)}
            </tr>
          </thead>
          <tbody>
            {tranches.map((t) => {
              const c = plan[t.name] || EMPTY;
              return (
                <tr key={t.name} className="border-b border-ink-800">
                  <td className="px-2 py-1.5 text-slate-300">{t.name}</td>
                  {cols.map(([k]) => (
                    <td key={k} className="px-1 py-1 text-right">
                      <input type="number" value={c[k]} onChange={(e) => patch(t.name, k, e.target.value)} className={numCls} />
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {data && (
        <>
          <div className="mt-4 mb-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-slate-400">
            <span>reorg equity value <CitedNumber cv={data.reorg_equity_value} className="text-slate-100" /></span>
            <span className="text-slate-500">ROI horizon {fmt(data.duration_years, 2)}y · entry = {data.entry_source}</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-ink-600">
                  <Th>Tranche</Th>
                  <Th right title="allowed claim: principal + accrued + make-whole">Claim</Th>
                  <Th right title="value of the plan package to this class">Plan value</Th>
                  <Th right title="plan value ÷ allowed claim">Recovery %</Th>
                  <Th right title="plan value per 100 of face — comparable to the market price">Per 100</Th>
                  <Th right title="annualized return from the market entry price to the plan value">ROI</Th>
                  <Th right title="absolute-priority recovery at the same reorg EV">Market %</Th>
                  <Th right title="plan recovery − absolute-priority recovery (positive = plan pays above APR)">Δ vs APR</Th>
                </tr>
              </thead>
              <tbody>
                {(data.rows || []).map((r) => (
                  <tr key={r.tranche} className="border-b border-ink-800 text-slate-300">
                    <td className="px-2 py-1.5">{r.tranche}</td>
                    <td className="px-2 py-1.5 text-right"><CitedNumber cv={r.claim} /></td>
                    <td className="px-2 py-1.5 text-right"><CitedNumber cv={r.plan_value} /></td>
                    <td className="px-2 py-1.5 text-right text-slate-100"><CitedNumber cv={r.recovery_pct} /></td>
                    <td className="px-2 py-1.5 text-right">
                      {r.recovery_per_100 ? <CitedNumber cv={r.recovery_per_100} /> : <span className="text-slate-600">—</span>}
                    </td>
                    <td className="px-2 py-1.5 text-right">
                      {r.roi ? <CitedNumber cv={r.roi} /> : <span className="text-slate-600" title="unquoted">—</span>}
                    </td>
                    <td className="px-2 py-1.5 text-right text-slate-400"><CitedNumber cv={r.market_pct} /></td>
                    <td className="px-2 py-1.5 text-right">
                      <span className={r.delta_pct?.value > 0 ? "text-emerald-300" : r.delta_pct?.value < 0 ? "text-rose-300" : "text-slate-500"}>
                        <CitedNumber cv={r.delta_pct} />
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-2 text-[11px] text-slate-500">
            A plan is an exogenous distribution — recovery here is what the plan grants, not an
            absolute-priority waterfall result. New debt is valued at the market % you set; cash at
            face; equity as your % of reorg equity value; rights at intrinsic worth.
          </div>
        </>
      )}
    </Section>
  );
}
