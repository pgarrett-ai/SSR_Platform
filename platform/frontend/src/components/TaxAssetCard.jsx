import React, { useState } from "react";
import { computeTax382 } from "../api.js";
import CitedNumber from "./CitedNumber.jsx";
import { Button, Section, fmt } from "../ui/index.jsx";

// NOL / §382 tax-asset card (Moyer ch. 11, F6): an ownership change on emergence limits how
// much of the pre-emergence NOL can shield future income (annual limit ≈ equity FMV × the
// long-term tax-exempt rate). Analyst sets the §382 knobs; equity FMV is derived from the
// shared plan assumptions. Server extracts the gross NOL from the filing (cited) and values
// the shield; the analyst can override the NOL when nothing is extracted.

const numCls =
  "w-24 rounded-md border border-ink-600 bg-ink-800 px-2 py-1 font-mono text-xs text-slate-100 outline-none focus:border-accent";

function TermField({ label, title, children }) {
  return (
    <label className="flex flex-col gap-1" title={title}>
      <span className="text-[10px] uppercase tracking-wide text-slate-500">{label}</span>
      {children}
    </label>
  );
}

export default function TaxAssetCard({ ticker, years, reorgEv, reorgDebt }) {
  const [nolOverride, setNolOverride] = useState("");   // $mm, optional
  const [rate, setRate] = useState(4.5);                // §382 long-term tax-exempt rate %
  const [taxRate, setTaxRate] = useState(21);           // marginal tax rate %
  const [horizon, setHorizon] = useState(20);           // years
  const [discRate, setDiscRate] = useState(12);         // discount rate %
  const [data, setData] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);

  // equity FMV is derived from the shared plan assumptions (read-only), sent as equity_fmv.
  const equityFmv = Math.max((Number(reorgEv) || 0) - (Number(reorgDebt) || 0), 0);

  async function run() {
    setRunning(true);
    setError(null);
    try {
      const d = await computeTax382(ticker, {
        nol: nolOverride === "" ? undefined : Number(nolOverride),
        equity_fmv: equityFmv,
        rate: (Number(rate) || 0) / 100,
        tax_rate: (Number(taxRate) || 0) / 100,
        horizon_years: Number(horizon) || 20,
        discount_rate: (Number(discRate) || 0) / 100,
      }, years);
      setData(d);
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  }

  const resultRows = data && data.available ? [
    ["Annual §382 limit", data.annual_limit, "equity FMV × long-term tax-exempt rate"],
    ["Usable NOL (over horizon)", data.usable_nol, "NOL absorbable within the horizon at the annual limit"],
    ["Stranded NOL", data.stranded_nol, "NOL not absorbable within the horizon at the annual limit"],
    ["Undiscounted shield", data.undiscounted_shield, "usable NOL × marginal tax rate"],
    ["Tax-asset PV", data.tax_asset_pv, "present value of the shield at the discount rate"],
  ] : [];

  const usable = data?.usable_nol?.value || 0;
  const stranded = data?.stranded_nol?.value || 0;
  const total = usable + stranded;
  const usablePct = total > 0 ? (usable / total) * 100 : 0;

  return (
    <Section
      title="NOL / §382 tax asset"
      subtitle="value the NOL shield after the emergence ownership change (Moyer ch. 11)"
    >
      <div className="mb-4 flex flex-wrap items-end gap-x-5 gap-y-3">
        <TermField label="NOL override $mm" title="override the filing-extracted gross NOL; leave blank to use the value tagged from the filing">
          <input type="number" step={10} value={nolOverride} onChange={(e) => setNolOverride(e.target.value)}
            className={numCls} placeholder="auto from filing" />
        </TermField>
        <TermField label="§382 rate %" title="long-term tax-exempt rate — annual limit = equity FMV × this rate">
          <input type="number" step={0.1} value={rate} onChange={(e) => setRate(e.target.value)} className={numCls} />
        </TermField>
        <TermField label="Tax rate %" title="marginal corporate tax rate applied to the usable NOL to size the cash shield">
          <input type="number" step={1} value={taxRate} onChange={(e) => setTaxRate(e.target.value)} className={numCls} />
        </TermField>
        <TermField label="Horizon yrs" title="years over which the NOL can be absorbed at the annual limit">
          <input type="number" step={1} value={horizon} onChange={(e) => setHorizon(e.target.value)} className={numCls} />
        </TermField>
        <TermField label="Discount %" title="rate used to present-value the tax shield">
          <input type="number" step={1} value={discRate} onChange={(e) => setDiscRate(e.target.value)} className={numCls} />
        </TermField>
        <TermField label="Equity FMV $mm" title="from plan assumptions: max(reorg EV − post-reorg debt, 0); drives the annual §382 limit">
          <input type="text" value={`${fmt(equityFmv, 0)}`} readOnly disabled
            className={`${numCls} opacity-70`} title="from plan assumptions (read-only)" />
        </TermField>
        <div className="pb-0.5">
          <Button variant="primary" onClick={run} disabled={running || equityFmv <= 0}>
            {running ? "Computing…" : "Run §382"}
          </Button>
        </div>
        {equityFmv <= 0 && (
          <span className="pb-2 text-xs text-slate-500">
            set reorg EV / post-reorg debt in the Plan Recovery card above — the §382 limit is sized off equity FMV
          </span>
        )}
        {error && <span className="pb-2 text-xs text-rose-300">{error}</span>}
      </div>

      {data && !data.available && (
        <div className="text-xs text-slate-400">
          {data.note}
          <div className="mt-1 text-slate-500">
            No NOL was extracted from the filings — enter the gross NOL in the NOL override field above, then re-run.
          </div>
        </div>
      )}

      {data && data.available && (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-slate-400">
            {data.nol_extracted ? (
              <span>
                filing NOL{" "}
                <CitedNumber cv={data.nol_extracted} className="text-slate-100" />{" "}
                <span className="text-slate-500">(gross, filing-tagged)</span>
              </span>
            ) : (
              <span>
                NOL <span className="font-mono text-slate-200">manual — {fmt(data.nol_used_mm, 0)} $mm</span>
              </span>
            )}
            <span className="text-slate-500">used in calc {fmt(data.nol_used_mm, 0)} $mm</span>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full max-w-xl text-sm">
              <tbody>
                {resultRows.map(([label, cv, title]) => (
                  <tr key={label} className="border-b border-ink-800">
                    <td className="px-2 py-1.5 text-slate-400" title={title}>{label}</td>
                    <td className="px-2 py-1.5 text-right text-slate-100"><CitedNumber cv={cv} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {total > 0 && (
            <div className="mt-4 max-w-xl">
              <div className="mb-1 flex justify-between text-[10px] uppercase tracking-wide text-slate-500">
                <span>usable {Math.round(usablePct)}%</span>
                <span>stranded {Math.round(100 - usablePct)}%</span>
              </div>
              <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-ink-800">
                <div className="h-2.5 bg-emerald-400/70" style={{ width: `${usablePct}%` }}
                  title="usable within the horizon" />
                <div className="h-2.5 bg-rose-400/60" style={{ width: `${100 - usablePct}%` }}
                  title="stranded — not absorbable within the horizon at the annual limit" />
              </div>
            </div>
          )}

          {data.note && <div className="mt-3 text-[11px] text-slate-500">{data.note}</div>}
        </>
      )}
    </Section>
  );
}
