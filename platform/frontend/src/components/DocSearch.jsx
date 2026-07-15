import React, { useState } from "react";
import { searchText } from "../api.js";
import { Badge, Button, Input, Section } from "../ui/index.jsx";

// Snippets come from filing text via FTS5 snippet(); the only HTML we allow through
// is our own <mark> markers — everything else is escaped before rendering.
function markOnly(snippet) {
  const esc = snippet
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
  return esc
    .replaceAll("&lt;mark&gt;", "<mark class=\"bg-accent/30 text-white rounded px-0.5\">")
    .replaceAll("&lt;/mark&gt;", "</mark>");
}

// Badge uppercases its label, so raw source kinds need display names ("mdna" → MD&A).
const KIND_LABELS = { mdna: "MD&A" };

// A hit's source kind → the Capital-page section it lives in.
const SECTION_IDS = { covenant: "covenants", obs: "obs", mdna: "mdna" };

// Full-text search over this issuer's analyzed filings (covenant clauses, OBS findings,
// MD&A). Lives on the Capital page next to the sections it searches; clicking a hit
// scrolls to the matching section.
export default function DocSearch({ ticker }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState(null);   // null = no search yet

  async function run(e) {
    e.preventDefault();
    if (!q.trim()) { setHits(null); return; }
    try {
      // The corpus stores the same clause across amendments/periods — dedupe for display.
      const seen = new Set();
      setHits((await searchText(q.trim(), ticker)).hits.filter((h) => {
        const k = `${h.source_kind}|${h.snippet}`;
        if (seen.has(k)) return false;
        seen.add(k);
        return true;
      }));
    } catch {
      setHits([]);
    }
  }

  return (
    <Section title="Document search" subtitle="covenants · OBS findings · MD&A for this issuer">
      <form onSubmit={run} className="flex gap-2">
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="e.g. springing lien, restricted payments, going concern"
          className="w-full"
        />
        <Button type="submit">Search</Button>
      </form>
      {hits !== null && (
        <div className="mt-3">
          <div className="mb-2 text-[10px] uppercase tracking-wide text-slate-600">
            {hits.length} hit{hits.length === 1 ? "" : "s"}
          </div>
          {hits.length === 0 && (
            <p className="text-sm text-slate-500">No matches in this issuer's analyzed filings.</p>
          )}
          {hits.map((h, i) => (
            <button
              key={i}
              onClick={() =>
                // plain scrollIntoView: smooth-behavior is a no-op under reduced-motion
                document.getElementById(SECTION_IDS[h.source_kind])?.scrollIntoView()}
              className="mb-1 block w-full rounded-md border border-ink-700 px-3 py-2 text-left text-sm hover:border-accent"
              title="jump to the matching section"
            >
              <Badge>{KIND_LABELS[h.source_kind] || h.source_kind}</Badge>
              <span
                className="ml-2 text-slate-400"
                dangerouslySetInnerHTML={{ __html: markOnly(h.snippet || "") }}
              />
            </button>
          ))}
        </div>
      )}
    </Section>
  );
}
