import React from "react";
import { Badge, Td, Th, rowClass } from "../ui/index.jsx";

// Confidence panel: LLM footnote totals reconciled against XBRL concepts.
export default function XbrlTieOut({ tieOuts }) {
  if (!tieOuts || tieOuts.length === 0) {
    return (
      <p className="text-sm text-slate-400">
        No tie-outs — needs both footnote and XBRL totals.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-ink-600">
            <Th>Total</Th>
            <Th right>Footnote (LLM)</Th>
            <Th right>XBRL</Th>
            <Th right>Δ</Th>
            <Th>Tie-out</Th>
          </tr>
        </thead>
        <tbody>
          {tieOuts.map((t, i) => {
            const ok = t.status === "match";
            return (
              <tr key={i} className={rowClass}>
                <Td className="text-slate-200">
                  {t.source_url ? (
                    <a href={t.source_url} target="_blank" rel="noreferrer" className="hover:text-accent hover:underline" title={t.xbrl_concept}>
                      {t.label}
                    </a>
                  ) : (
                    t.label
                  )}
                </Td>
                <Td right mono className="text-slate-300">{t.llm_display || "—"}</Td>
                <Td right mono className="text-slate-300">{t.xbrl_display || "—"}</Td>
                <Td right mono className={ok ? "text-slate-400" : "text-amber-300"}>
                  {t.delta_pct == null ? "—" : `${t.delta_pct > 0 ? "+" : ""}${t.delta_pct}%`}
                </Td>
                <Td>
                  <Badge tone={ok ? "ok" : "watch"}>{ok ? "✓ ties out" : "⚠ mismatch"}</Badge>
                </Td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-2 text-[11px] text-slate-500">
        Within 5% of the XBRL concept = ties out.
      </p>
    </div>
  );
}
