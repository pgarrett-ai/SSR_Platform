import React, { useMemo, useState } from "react";
import { fetchCompanyTimeline } from "../api.js";
import { useAsync } from "../cache.js";
import { Badge, Card, EmptyState, ErrorCard, Loading, Section, useShowMore } from "../ui/index.jsx";
import { DetectedStamp, SevBadge } from "../components/EventBits.jsx";

const FORM_TONE = { "10-K": "info", "10-Q": "accent", "8-K": "watch" }; // same map as risk/EventTimeline
const KINDS = [["event", "events"], ["filing", "filings"], ["changes", "what changed"]];
// same pill styling as the EventsPage type filters
const pillCls = (on) =>
  `rounded-full border px-2.5 py-0.5 text-[11px] ${
    on ? "border-accent text-white" : "border-ink-600 text-slate-400 hover:border-accent"
  }`;

export default function TimelinePage({ ticker, years }) {
  const { data, loading, error } = useAsync(
    `timeline:${ticker}:${years}`,
    () => fetchCompanyTimeline(ticker, years),
    [ticker, years],
  );
  const [kinds, setKinds] = useState([]);   // empty = all
  const filtered = useMemo(() => {
    const items = data?.items || [];
    return kinds.length ? items.filter((it) => kinds.includes(it.kind)) : items;
  }, [data, kinds]);
  const { shown, control } = useShowMore(filtered, 100, "entries");

  if (loading) return <Loading>Merging events, filings and changes…</Loading>;
  if (error) return <ErrorCard>{error}</ErrorCard>;
  if (!data) return null;

  const toggleKind = (k) =>
    setKinds((prev) => (prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k]));

  return (
    <Section flush title="Company timeline" subtitle="events · filings · what changed"
      right={
        <span className="flex gap-1.5">
          {KINDS.map(([k, label]) => (
            <button key={k} onClick={() => toggleKind(k)} className={pillCls(kinds.includes(k))}>
              {label}
            </button>
          ))}
        </span>
      }>
      {data.note && <div className="mb-3 text-xs text-amber-400">{data.note}</div>}
      <Card>
        <ul>
          {shown.map((it, i) => (
            <TimelineItem key={`${it.kind}-${it.id ?? it.accession_no ?? i}`} it={it} />
          ))}
        </ul>
        {control}
        {filtered.length === 0 && (
          <EmptyState hint={kinds.length ? "a kind filter is active" : undefined}>
            nothing yet — the ingestion daemon fills this as it runs
          </EmptyState>
        )}
      </Card>
    </Section>
  );
}

const ROW = "flex items-baseline gap-3 border-b border-ink-700/40 py-1.5 text-sm";

function TimelineItem({ it }) {
  const date = (
    <span className="w-24 shrink-0 font-mono text-xs text-slate-400">{it.date || "—"}</span>
  );
  if (it.kind === "event") {
    return (
      <li className={ROW}>
        {date}
        <SevBadge severity={it.severity} />
        <Badge tone="accent" mono>
          {it.source_form || it.event_type}{it.subtype ? ` ${it.subtype}` : ""}
        </Badge>
        {it.source_url ? (
          <a href={it.source_url} target="_blank" rel="noreferrer"
             className="truncate text-slate-200 hover:text-accent">
            {it.title || it.event_type}
          </a>
        ) : (
          <span className="truncate text-slate-300">{it.title || it.event_type}</span>
        )}
        <span className="ml-auto shrink-0">
          <DetectedStamp occurredAt={it.occurred_at} detectedAt={it.detected_at} />
        </span>
      </li>
    );
  }
  if (it.kind === "filing") {
    return (
      <li className={ROW}>
        {date}
        <Badge tone={FORM_TONE[it.form_type] || "neutral"} mono>{it.form_type}</Badge>
        {it.url ? (
          <a href={it.url} target="_blank" rel="noreferrer"
             className="truncate text-slate-400 hover:text-accent">
            {it.accession_no}
          </a>
        ) : (
          <span className="truncate text-slate-500">{it.accession_no}</span>
        )}
      </li>
    );
  }
  // kind === "changes": the what-changed card as one dated entry
  return (
    <li className={ROW}>
      {date}
      <Badge tone="watch">what changed</Badge>
      <div className="flex min-w-0 flex-wrap gap-2">
        {it.items.map((c) => (
          <span key={c.metric}
                className="rounded bg-ink-700 px-2 py-0.5 font-mono text-[11px] text-slate-300">
            {c.metric} {c.delta_pct > 0 ? "+" : ""}{c.delta_pct}%
            <span className={c.direction === "worse" ? "ml-1 text-rose-300" : "ml-1 text-emerald-300"}>
              {c.direction}
            </span>
          </span>
        ))}
      </div>
      <span className="ml-auto shrink-0 text-[10px] text-slate-600">{it.label}</span>
    </li>
  );
}
