import React from "react";
import { Badge, Td, Th, rowClass, useShowMore } from "../ui/index.jsx";

export default function SourcesPanel({ sources }) {
  const { shown, control } = useShowMore(sources || [], 12, "filings");
  if (!sources || sources.length === 0) return null;
  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-ink-600">
              <Th>Form</Th>
              <Th>Filed</Th>
              <Th>Period</Th>
              <Th right>Exhibits</Th>
              <Th right>Credit docs</Th>
              <Th>Accession</Th>
            </tr>
          </thead>
          <tbody>
            {shown.map((s) => (
              <tr key={s.accession_no} className={rowClass}>
                <Td>
                  <Badge mono>{s.form_type}</Badge>
                </Td>
                <Td mono className="text-[12px] text-slate-300">{s.filing_date}</Td>
                <Td mono className="text-[12px] text-slate-400">{s.period_of_report || "—"}</Td>
                <Td right mono className="text-[12px] text-slate-400">{s.n_exhibits}</Td>
                <Td right mono className="text-[12px]">
                  {s.n_credit_docs > 0 ? (
                    <span className="text-accent">{s.n_credit_docs}</span>
                  ) : (
                    <span className="text-slate-600">0</span>
                  )}
                </Td>
                <Td>
                  <a
                    href={s.filing_index_url}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-[11px] text-accent hover:underline"
                  >
                    {s.accession_no} ↗
                  </a>
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {control}
    </div>
  );
}
