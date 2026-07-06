import React from "react";

function Stat({ label, value }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wide text-slate-500">{label}</span>
      <span className="font-mono text-sm text-slate-200">{value ?? "—"}</span>
    </div>
  );
}

export default function Header({ header }) {
  if (!header) return null;
  const renamed =
    header.resolved_ticker && header.resolved_ticker.toUpperCase() !== header.ticker.toUpperCase();
  return (
    <div className="mb-8 rounded-xl border border-ink-700 bg-gradient-to-br from-ink-800 to-ink-900 p-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold text-white">{header.issuer || header.ticker}</h1>
            {header.from_cache && (
              <span className="rounded bg-emerald-500/15 px-2 py-0.5 text-[10px] uppercase text-emerald-300">
                cached
              </span>
            )}
          </div>
          <div className="mt-1 text-sm text-slate-400">
            <span className="font-mono text-slate-300">{header.ticker}</span>
            {renamed && (
              <span className="ml-1 text-amber-300/90">→ now {header.resolved_ticker}</span>
            )}
            <span className="mx-2 text-slate-600">·</span>
            CIK {header.cik}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-x-8 gap-y-2 sm:grid-cols-4">
          <Stat label="Lookback" value={`${header.years}y`} />
          <Stat label="Filings" value={header.n_filings} />
          <Stat label="LLM extraction" value={header.llm_enabled ? "on" : "off"} />
          <Stat
            label="Updated"
            value={header.last_updated ? header.last_updated.slice(0, 10) : "—"}
          />
        </div>
      </div>
    </div>
  );
}
