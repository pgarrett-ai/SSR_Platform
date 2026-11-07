import React from "react";
import { fetchEvents } from "../../api.js";
import { useAsync } from "../../cache.js";
import { Badge, Section } from "../../ui/index.jsx";

const SEV_TONE = { 1: "neutral", 2: "info", 3: "watch", 4: "watch", 5: "high" };
const SEV_LEGEND = "S5 bankruptcy, acceleration, restatement · S4 auditor change, delisting · " +
  "S3 material credit or operating fact · S2 routine agreement or officer change · S1 other";

// The issuer's EDGAR events (8-K items, 13D/G, NT filings, delistings, rating defaults),
// severity 2+ so routine insider Form 4s stay out, newest first. Nothing renders when there
// are none; a ticker the store does not know answers 404, which reads the same.
export default function EightKEvents({ ticker }) {
  const { data } = useAsync(`events:${ticker}`,
    () => fetchEvents({ ticker, min_severity: 2, limit: 20 }), [ticker]);
  const rows = [...(data?.events || [])]
    .sort((a, b) => (b.occurred_at || "").localeCompare(a.occurred_at || ""));
  if (!rows.length) return null;

  return (
    <Section title="Events" subtitle="EDGAR filings that move credit risk" collapsible defaultOpen={false}>
      <ul>
        {rows.map((e) => (
          <li key={e.id} className="flex items-baseline gap-3 border-b border-ink-700/40 py-1.5 text-sm">
            <span className="w-24 shrink-0 font-mono text-xs text-slate-400">
              {e.occurred_at?.slice(0, 10) || "—"}
            </span>
            <span title={SEV_LEGEND}>
              <Badge tone={SEV_TONE[e.severity] || "neutral"} mono>S{e.severity}</Badge>
            </span>
            <Badge tone="accent" mono>
              {e.source_form || e.event_type}{e.subtype ? ` ${e.subtype}` : ""}
            </Badge>
            {e.source_url ? (
              <a href={e.source_url} target="_blank" rel="noreferrer"
                className="truncate text-slate-200 hover:text-accent">
                {e.title || e.event_type}
              </a>
            ) : (
              <span className="truncate text-slate-300">{e.title || e.event_type}</span>
            )}
          </li>
        ))}
      </ul>
    </Section>
  );
}
