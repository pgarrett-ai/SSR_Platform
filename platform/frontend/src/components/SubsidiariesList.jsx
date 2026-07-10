import React, { useState } from "react";
import { Td, Th, rowClass } from "../ui/index.jsx";

// Legal-entity list parsed from Exhibit 21. Seeds the Recovery entity tree.
export default function SubsidiariesList({ subsidiaries }) {
  const [showAll, setShowAll] = useState(false);
  if (!subsidiaries || subsidiaries.length === 0) {
    return (
      <p className="text-sm text-slate-400">
        No Exhibit 21 entity list — LLM extraction off.
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
            <tr className="border-b border-ink-600">
              <Th>Entity</Th>
              <Th>Jurisdiction</Th>
              <Th>Parent</Th>
              <Th right>% owned</Th>
            </tr>
          </thead>
          <tbody>
            {shown.map((s, i) => (
              <tr key={i} className={rowClass}>
                <Td className="text-slate-200">{s.name}</Td>
                <Td className="text-slate-400">{s.jurisdiction || "—"}</Td>
                <Td className="text-slate-500">{s.parent || "—"}</Td>
                <Td right mono className="text-slate-400">
                  {s.percent_owned == null ? "—" : `${s.percent_owned}%`}
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
        <span>
          {subsidiaries.length} legal entities · seeds the Recovery entity tree
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
