import React, { useState } from "react";
import { Badge, Card, Section } from "../../ui/index.jsx";

const FORM_TONE = { "10-K": "info", "10-Q": "accent", "8-K": "watch" };

export default function EventTimeline({ data }) {
  const filings = data.filings || [];
  const [showAll, setShowAll] = useState(false);
  if (filings.length === 0) return null;
  const shown = showAll ? filings : filings.slice(0, 12);
  return (
    <Section flush title="SEC filing timeline" subtitle="10-K / 10-Q / 8-K">
      <Card className="max-h-72 overflow-y-auto">
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
        {filings.length > 12 && (
          <button
            onClick={() => setShowAll(!showAll)}
            className="mt-2 text-[11px] text-slate-500 hover:text-slate-300"
          >
            {showAll ? "show fewer" : `show all ${filings.length}`}
          </button>
        )}
      </Card>
    </Section>
  );
}
