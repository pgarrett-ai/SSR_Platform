import React from "react";
import { Badge, Card, Section, useShowMore } from "../../ui/index.jsx";

const FORM_TONE = { "10-K": "info", "10-Q": "accent", "8-K": "watch" };

export default function EventTimeline({ data }) {
  const filings = data.filings || [];
  const { shown, control } = useShowMore(filings, 12, "filings");
  if (filings.length === 0) return null;
  return (
    <Section flush title="SEC filing timeline" subtitle="10-K / 10-Q / 8-K">
      <Card>
        <ul className="space-y-1">
          {shown.map((f) => (
            <li key={f.accession_no} className="flex items-center gap-3 text-sm py-1 border-b border-ink-700/40">
              <span className="text-xs font-mono text-slate-400 w-24">{f.filing_date || "—"}</span>
              <Badge tone={FORM_TONE[f.form_type] || "neutral"} mono>{f.form_type}</Badge>
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
        {control}
      </Card>
    </Section>
  );
}
