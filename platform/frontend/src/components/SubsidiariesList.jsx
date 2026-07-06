import React, { useState } from "react";

// Phase 4.5: legal-entity list parsed from Exhibit 21. Seeds Fulcrum entities (Recovery tab).
export default function SubsidiariesList({ subsidiaries }) {
  const [showAll, setShowAll] = useState(false);
  if (!subsidiaries || subsidiaries.length === 0) {
    return (
      <p className="text-sm text-slate-400">
        The legal-entity list is parsed from the 10-K's Exhibit 21 when an Anthropic API key is set.
      </p>
    );
  }
  const src = subsidiaries.find((s) => s.citation?.source_url)?.citation;
  const shown = showAll ? subsidiaries : subsidiaries.slice(0, 24);
  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-ink-600 text-slate-400">
              <th className="py-2 pr-3 text-left font-medium">Subsidiary</th>
              <th className="py-2 px-3 text-left font-medium">Jurisdiction</th>
              <th className="py-2 px-3 text-left font-medium">Parent</th>
              <th className="py-2 px-3 text-right font-medium">% owned</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((s, i) => (
              <tr key={i} className="border-b border-ink-700/60">
                <td className="py-1.5 pr-3 text-slate-200">{s.name}</td>
                <td className="py-1.5 px-3 text-slate-400">{s.jurisdiction || "—"}</td>
                <td className="py-1.5 px-3 text-slate-500">{s.parent || "—"}</td>
                <td className="py-1.5 px-3 text-right font-mono text-slate-400">
                  {s.percent_owned == null ? "—" : `${s.percent_owned}%`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
        <span>
          {subsidiaries.length} legal entities · seed Fulcrum entities on the Recovery tab
          {src?.source_url && (
            <>
              {" · "}
              <a href={src.source_url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                ↗ Exhibit 21
              </a>
            </>
          )}
        </span>
        {subsidiaries.length > 24 && (
          <button onClick={() => setShowAll((v) => !v)} className="text-accent hover:underline">
            {showAll ? "show fewer" : `show all ${subsidiaries.length}`}
          </button>
        )}
      </div>
    </div>
  );
}
