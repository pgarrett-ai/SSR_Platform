import React from "react";

const SEV = {
  high: { ring: "border-rose-500/50", chip: "bg-rose-500/20 text-rose-300", label: "high" },
  watch: { ring: "border-amber-500/50", chip: "bg-amber-500/20 text-amber-300", label: "watch" },
  info: { ring: "border-sky-500/40", chip: "bg-sky-500/20 text-sky-300", label: "info" },
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
    <div className={`rounded-lg border ${sev.ring} bg-ink-800/70 p-4`}>
      <div className="mb-2 flex items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-slate-100">
          {TITLES[flag.flag_type] || flag.flag_type}
        </h4>
        <span className={`rounded px-2 py-0.5 text-[10px] uppercase tracking-wide ${sev.chip}`}>
          {sev.label}
        </span>
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
          <span className="text-slate-500">Read next →</span> {flag.pointer}
        </p>
      )}
    </div>
  );
}
