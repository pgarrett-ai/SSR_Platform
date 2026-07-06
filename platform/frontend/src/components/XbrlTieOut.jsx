import React from "react";

// Phase 4.3 confidence panel: LLM footnote totals reconciled against XBRL concepts.
export default function XbrlTieOut({ tieOuts }) {
  if (!tieOuts || tieOuts.length === 0) {
    return (
      <p className="text-sm text-slate-400">
        Reconciliation of footnote-extracted totals (leases, pension, debt) against their XBRL
        concepts appears here once both are available.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-ink-600 text-slate-400">
            <th className="py-2 pr-3 text-left font-medium">Total</th>
            <th className="py-2 px-3 text-right font-medium">Footnote (LLM)</th>
            <th className="py-2 px-3 text-right font-medium">XBRL</th>
            <th className="py-2 px-3 text-right font-medium">Δ</th>
            <th className="py-2 px-3 text-left font-medium">Tie-out</th>
          </tr>
        </thead>
        <tbody>
          {tieOuts.map((t, i) => {
            const ok = t.status === "match";
            return (
              <tr key={i} className="border-b border-ink-700/60">
                <td className="py-2 pr-3 text-slate-200">
                  {t.source_url ? (
                    <a href={t.source_url} target="_blank" rel="noreferrer" className="hover:text-accent hover:underline" title={t.xbrl_concept}>
                      {t.label}
                    </a>
                  ) : (
                    t.label
                  )}
                </td>
                <td className="py-2 px-3 text-right font-mono text-slate-300">{t.llm_display || "—"}</td>
                <td className="py-2 px-3 text-right font-mono text-slate-300">{t.xbrl_display || "—"}</td>
                <td className={`py-2 px-3 text-right font-mono ${ok ? "text-slate-400" : "text-amber-300"}`}>
                  {t.delta_pct == null ? "—" : `${t.delta_pct > 0 ? "+" : ""}${t.delta_pct}%`}
                </td>
                <td className="py-2 px-3">
                  <span className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${ok ? "bg-emerald-500/15 text-emerald-300" : "bg-amber-500/15 text-amber-300"}`}>
                    {ok ? "✓ ties out" : "⚠ mismatch"}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-2 text-[11px] text-slate-500">
        Confidence score: a footnote total within 5% of its XBRL concept ties out. A mismatch means
        the LLM reading and the structured fact disagree — verify before trusting the number.
      </p>
    </div>
  );
}
