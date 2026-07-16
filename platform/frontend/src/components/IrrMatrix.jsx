import React, { useEffect, useState } from "react";
import { Section, Th } from "../ui/index.jsx";

// Duration-sensitive IRR (Moyer ch. 9/12): a recovery estimate is meaningless without
// time — annualized return = (recovery / entry price)^(1/T) − 1 across entry prices and
// case durations, shaded where it clears the 15–25% distressed hurdle band.

const DURATIONS = [0.5, 1, 2, 3];
const PRICE_STEPS = [-10, -5, 0, 5, 10];

const cellTone = (irr) =>
  irr == null ? "" :
  irr >= 0.25 ? "bg-emerald-500/20 text-emerald-200" :
  irr >= 0.15 ? "bg-emerald-500/10 text-emerald-300" :
  irr >= 0 ? "text-slate-300" : "text-rose-300";

export default function IrrMatrix({ tranches, quotedByName }) {
  const withRecovery = (tranches || []).filter((t) => t.face > 0);
  const [name, setName] = useState(withRecovery[0]?.tranche);
  const [entry, setEntry] = useState(null);

  const t = withRecovery.find((x) => x.tranche === name) || withRecovery[0];
  const quote = t ? quotedByName?.[t.tranche] : null;

  useEffect(() => {
    // default entry: matched drop-file quote, else 50
    setEntry(quote?.last_price ?? 50);
  }, [name, quote?.last_price]);

  if (!t) return null;
  const recovery = (t["mean_recovery_$"] / t.face) * 100; // per 100 of face

  return (
    <Section
      title="Duration-sensitive IRR"
      subtitle="annualized return by entry price × case duration · 15–25% hurdle band shaded (Moyer)"
    >
      <div className="mb-3 flex flex-wrap items-end gap-4 text-xs">
        <label className="flex flex-col gap-1">
          <span className="text-[10px] uppercase tracking-wide text-slate-500">Tranche</span>
          <select value={t.tranche} onChange={(e) => setName(e.target.value)}
            className="max-w-[280px] rounded-md border border-ink-600 bg-ink-800 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-accent">
            {withRecovery.map((x) => (
              <option key={x.tranche} value={x.tranche}>{x.tranche}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[10px] uppercase tracking-wide text-slate-500">
            Entry price (per 100){quote ? ` · quote ${quote.last_price} as of ${quote.as_of}` : " · no matched quote"}
          </span>
          <input type="number" step={1} value={entry ?? ""}
            onChange={(e) => setEntry(e.target.value === "" ? null : Number(e.target.value))}
            className="w-28 rounded-md border border-ink-600 bg-ink-800 px-2 py-1.5 font-mono text-xs text-slate-100 outline-none focus:border-accent" />
        </label>
        <div className="text-slate-500">
          expected recovery <span className="font-mono text-slate-200">{recovery.toFixed(0)}</span> per 100 face
          <span className="text-slate-600"> (simulation mean, % of face)</span>
        </div>
      </div>

      <table className="border-collapse text-xs">
        <thead>
          <tr className="border-b border-ink-600">
            <Th>Entry ↓ / exit in</Th>
            {DURATIONS.map((d) => (<Th key={d} right>{d}y</Th>))}
          </tr>
        </thead>
        <tbody>
          {PRICE_STEPS.map((step) => {
            const p = (entry ?? 0) + step;
            if (p <= 0) return null;
            return (
              <tr key={step} className="border-b border-ink-700/60">
                <td className={`px-2 py-1.5 font-mono ${step === 0 ? "text-slate-100" : "text-slate-400"}`}>
                  {p.toFixed(0)}{step === 0 ? " ←" : ""}
                </td>
                {DURATIONS.map((d) => {
                  const irr = Math.pow(recovery / p, 1 / d) - 1;
                  return (
                    <td key={d}
                      title={`(${recovery.toFixed(0)} ÷ ${p.toFixed(0)})^(1/${d}) − 1`}
                      className={`px-3 py-1.5 text-right font-mono ${cellTone(irr)}`}>
                      {(100 * irr).toFixed(0)}%
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="mt-2 text-[11px] text-slate-500">
        recovery held at the simulation mean — duration only reprices time value, not outcome
      </div>
    </Section>
  );
}
