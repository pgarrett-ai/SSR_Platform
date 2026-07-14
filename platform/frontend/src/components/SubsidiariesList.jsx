import React, { useState } from "react";
import { Badge, Td, Th, rowClass } from "../ui/index.jsx";

// Legal-entity list from Exhibit 21, with the structural-subordination read: entities that
// obligate debt (matched from XBRL obligor tagging) are flagged, guarantor classes from the
// credit agreements render above the table, and the list seeds the Recovery entity tree.
export default function SubsidiariesList({ subsidiaries, guarantorNotes }) {
  const [showAll, setShowAll] = useState(false);
  if (!subsidiaries || subsidiaries.length === 0) {
    return (
      <p className="text-sm text-slate-400">
        No Exhibit 21 entity list — LLM extraction off.
      </p>
    );
  }
  const src = subsidiaries.find((s) => s.citation?.source_url)?.citation;
  // debt obligors first — they're the entities that matter for structural subordination
  const ordered = [...subsidiaries].sort((a, b) => (b.role ? 1 : 0) - (a.role ? 1 : 0));
  const shown = showAll ? ordered : ordered.slice(0, 24);
  // hide columns Exhibit 21 didn't populate for any entity — a column of "—" says nothing
  const hasRole = subsidiaries.some((s) => s.role);
  const hasParent = subsidiaries.some((s) => s.parent);
  const hasPct = subsidiaries.some((s) => s.percent_owned != null);
  return (
    <div>
      {guarantorNotes?.length > 0 && (
        <div className="mb-3 rounded-md border border-ink-700 bg-ink-900/50 p-3 text-[12px] text-slate-300">
          <div className="mb-1 text-[11px] uppercase tracking-wide text-slate-500">
            Guarantors (per the credit documents)
          </div>
          {guarantorNotes.map((g, i) => (
            <div key={i}>{g}</div>
          ))}
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-ink-600">
              <Th>Entity</Th>
              {hasRole && <Th>Role</Th>}
              <Th>Jurisdiction</Th>
              {hasParent && <Th>Parent</Th>}
              {hasPct && <Th right>% owned</Th>}
            </tr>
          </thead>
          <tbody>
            {shown.map((s, i) => (
              <tr key={i} className={rowClass}>
                <Td className="text-slate-200">{s.name}</Td>
                {hasRole && (
                  <Td>
                    {s.role ? (
                      <Badge
                        tone={s.role === "debt obligor" ? "accent" : "neutral"}
                        className="cursor-help"
                        title={s.instruments?.length ? `obligates: ${s.instruments.join(", ")}` : undefined}
                      >
                        {s.role}
                      </Badge>
                    ) : (
                      <span className="text-slate-600">—</span>
                    )}
                  </Td>
                )}
                <Td className="text-slate-400">{s.jurisdiction || "—"}</Td>
                {hasParent && <Td className="text-slate-500">{s.parent || "—"}</Td>}
                {hasPct && (
                  <Td right mono className="text-slate-400">
                    {s.percent_owned == null ? "—" : `${s.percent_owned}%`}
                  </Td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
        <span>
          {subsidiaries.length} legal entities · debt at an entity is served by that entity's
          value first (structural subordination) · seeds the Recovery entity tree
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
