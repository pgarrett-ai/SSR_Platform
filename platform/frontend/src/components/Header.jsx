import React from "react";
import { Badge, Stat } from "../ui/index.jsx";

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
            {header.from_cache && <Badge tone="ok">cached</Badge>}
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
          <Stat bare label="Lookback" value={`${header.years}y`} />
          <Stat bare label="Filings" value={header.n_filings} />
          <Stat bare label="LLM extraction" value={header.llm_enabled ? "on" : "off"} />
          <Stat bare label="Updated" value={header.last_updated ? header.last_updated.slice(0, 10) : "—"} />
        </div>
      </div>
    </div>
  );
}
