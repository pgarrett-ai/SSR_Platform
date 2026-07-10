import React from "react";
import { Badge } from "../ui/index.jsx";

const SEV = {
  high: { ring: "border-rose-500/50", tone: "high" },
  watch: { ring: "border-amber-500/50", tone: "watch" },
  info: { ring: "border-sky-500/40", tone: "info" },
};

const TITLES = {
  ap_outrunning_revenue: "Payables stretching — possible supplier finance",
  ebitda_vs_ocf_divergence: "EBITDA not converting to cash",
  cash_up_no_debt: "Cash rising without new reported debt",
  negative_fcf_burn: "Negative free cash flow — liquidity runway",
};

export default function FlagCard({ flag }) {
  const sev = SEV[flag.severity] || SEV.info;
  return (
    <div className={`rounded-xl border ${sev.ring} bg-ink-800/50 p-4`}>
      <div className="mb-2 flex items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-slate-100">
          {TITLES[flag.flag_type] || flag.flag_type}
        </h4>
        <Badge tone={sev.tone}>{flag.severity}</Badge>
      </div>
      <p className="text-[13px] leading-relaxed text-slate-300">{flag.narrative}</p>
      {flag.metrics && Object.keys(flag.metrics).length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {Object.entries(flag.metrics)
            .filter(([, v]) => v !== null && v !== undefined)
            .map(([k, v]) => (
              <span
                key={k}
                className="rounded bg-ink-700 px-2 py-1 font-mono text-[11px] text-slate-300"
              >
                <span className="text-slate-500">{k}:</span> {String(v)}
              </span>
            ))}
        </div>
      )}
      {flag.pointer && (
        <p className="mt-3 border-t border-ink-700 pt-2 text-[12px] text-slate-400">
          <span className="text-slate-500">Refs:</span> {flag.pointer}
        </p>
      )}
    </div>
  );
}
