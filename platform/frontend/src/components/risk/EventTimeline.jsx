import React from "react";
import { Card, Section } from "../../ui/index.jsx";

const BADGE = {
  "10-K": "bg-blue-500/20 text-blue-300",
  "10-Q": "bg-teal-500/20 text-teal-300",
  "8-K": "bg-amber-500/20 text-amber-300",
};

export default function EventTimeline({ data }) {
  const filings = (data.filings || []).slice(0, 40);
  if (filings.length === 0) return null;
  return (
    <Section flush title="SEC filing timeline" subtitle="10-K / 10-Q / 8-K">
      <Card className="max-h-72 overflow-y-auto">
        <ul className="space-y-1">
          {filings.map((f) => (
            <li key={f.accession_no} className="flex items-center gap-3 text-sm py-1 border-b border-ink-700/40">
              <span className="text-xs font-mono text-slate-400 w-24">{f.filing_date || "—"}</span>
              <span className={`text-xs px-2 py-0.5 rounded ${BADGE[f.form_type] || "bg-slate-600/30 text-slate-300"}`}>
                {f.form_type}
              </span>
              {f.url ? (
                <a href={f.url} target="_blank" rel="noreferrer" className="text-slate-400 hover:text-accent truncate">
                  {f.accession_no}
                </a>
              ) : (
                <span className="text-slate-500 truncate">{f.accession_no}</span>
              )}
            </li>
          ))}
        </ul>
      </Card>
    </Section>
  );
}
