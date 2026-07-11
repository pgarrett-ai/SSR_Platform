import React, { useEffect, useState } from "react";
import { fetchScreen, searchText } from "../api.js";
import { Badge, Td, Th, fmtX, rowClass } from "../ui/index.jsx";

const fmtB = (v) => (v == null ? "—" : `$${(v / 1e9).toFixed(1)}B`);

// Snippets come from filing text via FTS5 snippet(); the only HTML we allow through
// is our own <mark> markers — everything else is escaped before rendering.
function markOnly(snippet) {
  const esc = snippet
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
  return esc
    .replaceAll("&lt;mark&gt;", "<mark class=\"bg-accent/30 text-white rounded px-0.5\">")
    .replaceAll("&lt;/mark&gt;", "</mark>");
}

export default function ScreenTable({ onPick }) {
  const [rows, setRows] = useState([]);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState(null);   // null = no search yet

  useEffect(() => {
    fetchScreen().then(setRows).catch(() => {});
  }, []);

  async function runSearch(e) {
    e.preventDefault();
    if (!q.trim()) { setHits(null); return; }
    try {
      setHits((await searchText(q.trim())).hits);
    } catch {
      setHits([]);
    }
  }

  return (
    <div className="mt-12 text-left">
      <form onSubmit={runSearch} className="mb-6 flex gap-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="search covenants, OBS findings, MD&A… (e.g. springing lien)"
          className="w-full rounded-md border border-ink-600 bg-ink-800 px-3 py-1.5 text-sm text-slate-100 outline-none focus:border-accent"
        />
        <button type="submit" className="rounded-md border border-ink-600 px-3 py-1.5 text-sm text-slate-300 hover:border-accent hover:text-white">
          Search
        </button>
      </form>

      {hits !== null && (
        <div className="mb-8">
          <div className="mb-2 text-[10px] uppercase tracking-wide text-slate-600">
            {hits.length} hit{hits.length === 1 ? "" : "s"}
          </div>
          {hits.length === 0 && <p className="text-sm text-slate-500">No matches in the analyzed corpus.</p>}
          {hits.map((h, i) => (
            <button
              key={i}
              onClick={() => onPick(h.ticker)}
              className="mb-1 block w-full rounded-md border border-ink-700 px-3 py-2 text-left text-sm hover:border-accent"
            >
              <span className="font-mono text-slate-200">{h.ticker}</span>
              <Badge className="ml-2">{h.source_kind}</Badge>
              <span
                className="ml-2 text-slate-400"
                dangerouslySetInnerHTML={{ __html: markOnly(h.snippet || "") }}
              />
            </button>
          ))}
        </div>
      )}

      {rows.length > 0 && (
        <>
          <div className="mb-2 text-[10px] uppercase tracking-wide text-slate-600">Analyzed companies</div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-ink-600">
                  <Th>Ticker</Th>
                  <Th>Issuer</Th>
                  <Th right>Reported lev</Th>
                  <Th right>Economic lev</Th>
                  <Th right>Net econ debt</Th>
                  <Th right>Flags</Th>
                  <Th right className="cursor-help" title="composite risk 0-100 · trained PD implied rating — fills in after a Default Risk run">Risk</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.ticker}
                    onClick={() => onPick(r.ticker)}
                    className={`cursor-pointer ${rowClass}`}
                  >
                    <Td mono className="text-slate-200">{r.ticker}</Td>
                    <Td className="text-slate-400">{r.issuer || "—"}</Td>
                    <Td right mono className="text-slate-300">{fmtX(r.reported_leverage)}</Td>
                    <Td right mono className="text-slate-300">{fmtX(r.economic_leverage)}</Td>
                    <Td right mono className="text-slate-400">{fmtB(r.net_economic_debt)}</Td>
                    <Td right mono className="text-slate-400">{r.flag_count ?? "—"}</Td>
                    <Td right mono className="text-slate-300">
                      {r.overall_risk == null ? "—" : `${r.overall_risk.toFixed(1)}${r.implied_rating ? ` · ${r.implied_rating}` : ""}`}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-2 text-[11px] text-slate-500">
            one row per issuer · latest snapshot
          </div>
        </>
      )}
    </div>
  );
}
