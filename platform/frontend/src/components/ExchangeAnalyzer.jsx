import React, { useMemo, useState } from "react";
import { exchangeRecovery } from "../api.js";
import CitedNumber from "./CitedNumber.jsx";
import { Badge, Button, Section, Th, fmt } from "../ui/index.jsx";

// Exchange-offer analyzer (Moyer ch. 11): a calculator over typed offer terms — the
// SC TO-I/S-4 parser is Phase-6 backlog. Server returns holdout/tender payoff curves
// per participation level over the base structure's EV grid; the slider and matrix
// read the curves client-side. Runs on the edited structure (EvExplorer convention).

function TermField({ label, title, children }) {
  return (
    <label className="flex flex-col gap-1" title={title}>
      <span className="text-[10px] uppercase tracking-wide text-slate-500">{label}</span>
      {children}
    </label>
  );
}

const numCls =
  "w-24 rounded-md border border-ink-600 bg-ink-800 px-2 py-1.5 font-mono text-xs text-slate-100 outline-none focus:border-accent";

export default function ExchangeAnalyzer({ ticker, years, structure, baseEbitda, accrualYears }) {
  const tranches = structure?.tranches || [];
  const [target, setTarget] = useState("");
  const [ratio, setRatio] = useState(50);
  const [seniority, setSeniority] = useState("priming");
  const [couponPct, setCouponPct] = useState(0);
  const [cash100, setCash100] = useState(0);
  const [equityPct, setEquityPct] = useState(0);
  const [minTender, setMinTender] = useState(50);
  const [userP, setUserP] = useState(90);
  const [exitConsent, setExitConsent] = useState(false);
  const [data, setData] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);
  const [idx, setIdx] = useState(120);

  const effTarget = target || tranches[0]?.name || "";

  async function run() {
    setRunning(true);
    setError(null);
    try {
      const d = await exchangeRecovery(ticker, {
        structure,
        sim: { base_ebitda: baseEbitda, accrual_years: accrualYears },
        target: effTarget,
        ratio_pct: ratio,
        participation_pct: userP,
        seniority,
        coupon_pct: couponPct,
        cash_per_100: cash100,
        equity_pct_at_full: equityPct,
        min_tender_pct: minTender,
        exit_consent: exitConsent,
      }, years);
      setData(d);
      // default EV: 6.0x EBITDA when positive, else the grid midpoint
      let i0 = Math.floor((d.ev_grid?.length || 241) / 2);
      if (d.ebitda > 0 && d.ev_grid) {
        const want = 6.0 * d.ebitda;
        i0 = d.ev_grid.reduce(
          (best, v, i) => (Math.abs(v - want) < Math.abs(d.ev_grid[best] - want) ? i : best), 0);
      }
      setIdx(i0);
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  }

  const ev = data?.ev_grid?.[idx];
  const mult = data?.multiple_grid?.[idx];
  const rows = useMemo(() => {
    if (!data) return [];
    const basePct = data.base_pct?.[idx];
    return (data.scenarios || []).map((s) => {
      const tender = s.fails ? basePct : s.tender?.[idx];
      const holdout = s.fails ? basePct : s.holdout?.[idx];
      return {
        ...s,
        tenderAt: tender,
        holdoutAt: holdout,
        delta: tender != null && holdout != null ? tender - holdout : null,
      };
    });
  }, [data, idx]);
  const userRow = rows.find((r) => Math.abs(r.participation_pct - userP) < 1e-6);

  return (
    <Section
      title="Exchange analyzer"
      subtitle="holdout vs tender payoff per participation level — offer terms are your input (Moyer ch. 11)"
    >
      <div className="flex flex-wrap items-end gap-x-5 gap-y-4">
        <TermField label="Target tranche">
          <select value={effTarget} onChange={(e) => setTarget(e.target.value)}
            className="max-w-[240px] rounded-md border border-ink-600 bg-ink-800 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-accent">
            {tranches.map((t) => (
              <option key={t.name} value={t.name}>{t.name}</option>
            ))}
          </select>
        </TermField>
        <TermField label="Ratio (new per 100)" title="new face issued per 100 of old face tendered">
          <input type="number" step={5} value={ratio} onChange={(e) => setRatio(Number(e.target.value))} className={numCls} />
        </TermField>
        <TermField label="New-paper seniority"
          title="priming = rank ahead of every lien; second lien = behind existing secured; maturity-based seniority not modeled">
          <select value={seniority} onChange={(e) => setSeniority(e.target.value)}
            className="rounded-md border border-ink-600 bg-ink-800 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-accent">
            <option value="priming">priming</option>
            <option value="second_lien">second lien</option>
            <option value="unsecured">unsecured</option>
          </select>
        </TermField>
        <TermField label="Coupon %">
          <input type="number" step={0.25} value={couponPct} onChange={(e) => setCouponPct(Number(e.target.value))} className={numCls} />
        </TermField>
        <TermField label="Cash / 100" title="cash consideration per 100 tendered — valued at face, does not deplete waterfall EV">
          <input type="number" step={1} value={cash100} onChange={(e) => setCash100(Number(e.target.value))} className={numCls} />
        </TermField>
        <TermField label="Equity % at full" title="share of the equity residual to tendering holders at 100% participation; partial participation scales as p/(p+(1−e)/e)">
          <input type="number" step={1} value={equityPct} onChange={(e) => setEquityPct(Number(e.target.value))} className={numCls} />
        </TermField>
        <TermField label="Min tender %" title="offer fails below this participation — failed rows show base-structure values">
          <input type="number" step={5} value={minTender} onChange={(e) => setMinTender(Number(e.target.value))} className={numCls} />
        </TermField>
        <TermField label="Your participation %">
          <input type="number" step={5} value={userP} onChange={(e) => setUserP(Number(e.target.value))} className={numCls} />
        </TermField>
        <label className="flex items-center gap-1.5 pb-1.5 text-xs text-slate-400"
          title="exit consent: the stub is contractually subordinated to the new paper (single-hop) — the coercion mechanic">
          <input type="checkbox" checked={exitConsent}
            onChange={(e) => setExitConsent(e.target.checked)} className="accent-accent" />
          exit consent
        </label>
        <div className="pb-0.5">
          <Button variant="primary" onClick={run} disabled={running || !effTarget}>
            {running ? "Computing…" : "Run analyzer"}
          </Button>
        </div>
        {error && <span className="pb-2 text-xs text-rose-300">{error}</span>}
      </div>

      {data && (
        <>
          <div className="mt-4 mb-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-slate-400">
            <label className="flex items-center gap-3">
              <span className="text-[10px] uppercase tracking-wide text-slate-500">EV $mm</span>
              <input type="range" min={0} max={data.ev_grid.length - 1} value={idx}
                onChange={(e) => setIdx(Number(e.target.value))} className="w-64 accent-accent" />
              <span className="font-mono text-slate-100">{fmt(ev, 0)}</span>
              {mult != null && <span className="font-mono text-slate-400">= {mult.toFixed(1)}x EBITDA</span>}
            </label>
            {userRow?.proforma_leverage != null && (
              <Badge tone="info" title={`pro-forma face ${fmt(userRow.proforma_face, 0)} $mm at your ${userP}% participation`}>
                pro-forma {userRow.proforma_leverage}x
              </Badge>
            )}
            {data.quote_premium && (
              <Badge tone={data.quote_premium.premium_per_100 > 0 ? "ok" : "watch"}
                title={`package ${data.quote_premium.package_at_ref} vs quote ${data.quote_premium.target_quote} at EV ${fmt(data.quote_premium.ref_ev_mm, 0)} $mm`}>
                package premium {fmt(data.quote_premium.premium_per_100)}
              </Badge>
            )}
            {data.holdout_runway_quarters && (
              <span className="text-slate-500">
                holdout runway <CitedNumber cv={data.holdout_runway_quarters} className="text-slate-300" />
              </span>
            )}
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-ink-600">
                  <Th right>Participation %</Th>
                  <Th right title="stub + new paper, per the offer terms">Pro-forma face</Th>
                  <Th right>Leverage</Th>
                  <Th right title="cash/100 + ratio × new-paper recovery % + equity slice per 100 old face">Tender / 100</Th>
                  <Th right title="the stub's recovery % of its allowed claim — the holdout keeps the old bond">Holdout / 100</Th>
                  <Th right title="tender − holdout at the slider EV (positive = the offer coerces)">Δ</Th>
                  <Th right title="EV where holding out overtakes tendering (piecewise-linear crossover)">Crossover EV</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.participation_pct}
                    className={`border-b border-ink-800 font-mono ${r.fails ? "text-slate-600" : "text-slate-300"}`}>
                    <td className="px-2 py-1.5 text-right">
                      {fmt(r.participation_pct)}
                      {Math.abs(r.participation_pct - userP) < 1e-6 && (
                        <span className="ml-1 text-[9px] uppercase text-accent">yours</span>
                      )}
                      {r.fails && (
                        <span className="ml-1 text-[9px] uppercase text-slate-600"
                          title={`below the ${data.min_tender_pct}% minimum-tender condition — base-structure values shown`}>
                          offer fails
                        </span>
                      )}
                    </td>
                    <td className="px-2 py-1.5 text-right">{r.fails ? "—" : fmt(r.proforma_face, 0)}</td>
                    <td className="px-2 py-1.5 text-right">
                      {r.fails || r.proforma_leverage == null ? "—" : `${r.proforma_leverage}x`}
                    </td>
                    <td className="px-2 py-1.5 text-right text-slate-100">{fmt(r.tenderAt)}</td>
                    <td className="px-2 py-1.5 text-right">{fmt(r.holdoutAt)}</td>
                    <td className={`px-2 py-1.5 text-right font-semibold ${
                      r.fails || r.delta == null ? "text-slate-600"
                        : r.delta > 0 ? "text-emerald-300" : r.delta < 0 ? "text-rose-300" : "text-slate-500"}`}>
                      {r.fails || r.delta == null ? "—" : `${r.delta > 0 ? "+" : ""}${fmt(r.delta)}`}
                    </td>
                    <td className="px-2 py-1.5 text-right">
                      {r.fails || r.crossover_ev == null ? "—" : fmt(r.crossover_ev, 0)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mt-2 text-[11px] text-slate-500">{data.note}</div>
        </>
      )}
    </Section>
  );
}
