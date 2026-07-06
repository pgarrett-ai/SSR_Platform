import React, { useState } from "react";

export default function SourcesPanel({ sources }) {
  const [showAll, setShowAll] = useState(false);
  if (!sources || sources.length === 0) return null;
  const shown = showAll ? sources : sources.slice(0, 12);
  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-ink-600 text-slate-400">
              <th className="py-2 pr-3 text-left font-medium">Form</th>
              <th className="py-2 pr-3 text-left font-medium">Filed</th>
              <th className="py-2 pr-3 text-left font-medium">Period</th>
              <th className="py-2 pr-3 text-right font-medium">Exhibits</th>
              <th className="py-2 pr-3 text-right font-medium">Credit docs</th>
              <th className="py-2 text-left font-medium">Accession</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((s) => (
              <tr key={s.accession_no} className="border-b border-ink-700/60 hover:bg-ink-700/30">
                <td className="py-1.5 pr-3">
                  <span className="rounded bg-ink-600 px-1.5 py-0.5 font-mono text-[11px] text-slate-200">
                    {s.form_type}
                  </span>
                </td>
                <td className="py-1.5 pr-3 font-mono text-[12px] text-slate-300">{s.filing_date}</td>
                <td className="py-1.5 pr-3 font-mono text-[12px] text-slate-400">
                  {s.period_of_report || "—"}
                </td>
                <td className="py-1.5 pr-3 text-right font-mono text-[12px] text-slate-400">
                  {s.n_exhibits}
                </td>
                <td className="py-1.5 pr-3 text-right font-mono text-[12px]">
                  {s.n_credit_docs > 0 ? (
                    <span className="text-accent">{s.n_credit_docs}</span>
                  ) : (
                    <span className="text-slate-600">0</span>
                  )}
                </td>
                <td className="py-1.5">
                  <a
                    href={s.filing_index_url}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-[11px] text-accent hover:underline"
                  >
                    {s.accession_no} ↗
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sources.length > 12 && (
        <button
          onClick={() => setShowAll((v) => !v)}
          className="mt-3 text-xs text-accent hover:underline"
        >
          {showAll ? "Show fewer" : `Show all ${sources.length} filings`}
        </button>
      )}
    </div>
  );
}
