import React, { useEffect, useState } from "react";
import { fetchScreen, searchText } from "../api.js";

const fmtX = (v) => (v == null ? "—" : `${v.toFixed(1)}x`);
const fmtB = (v) => (v == null ? "—" : `$${(v / 1e9).toFixed(1)}B`);
const fmt0 = (v) => (v == null ? "—" : v.toFixed(0));

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
    <div className="mx-auto mt-12 max-w-4xl text-left">
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
              <span className="ml-2 rounded-full border border-ink-600 px-1.5 text-[10px] uppercase text-slate-500">{h.source_kind}</span>
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
                <tr className="border-b border-ink-600 text-slate-400">
                  <th className="py-2 pr-3 text-left font-medium">Ticker</th>
                  <th className="py-2 px-3 text-left font-medium">Issuer</th>
                  <th className="py-2 px-3 text-right font-medium">Rep lev</th>
                  <th className="py-2 px-3 text-right font-medium">Econ lev</th>
                  <th className="py-2 px-3 text-right font-medium">Net econ debt</th>
                  <th className="py-2 px-3 text-right font-medium">Flags</th>
                  <th className="py-2 px-3 text-right font-medium">Tone</th>
                  <th className="py-2 px-3 text-right font-medium" title="composite risk 0-100 · trained PD implied rating — fills in after a Default Risk run">Risk</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.ticker}
                    onClick={() => onPick(r.ticker)}
                    className="cursor-pointer border-b border-ink-700/60 hover:bg-ink-800"
                  >
                    <td className="py-1.5 pr-3 font-mono text-slate-200">{r.ticker}</td>
                    <td className="py-1.5 px-3 text-slate-400">{r.issuer || "—"}</td>
                    <td className="py-1.5 px-3 text-right font-mono text-slate-300">{fmtX(r.reported_leverage)}</td>
                    <td className="py-1.5 px-3 text-right font-mono text-slate-300">{fmtX(r.economic_leverage)}</td>
                    <td className="py-1.5 px-3 text-right font-mono text-slate-400">{fmtB(r.net_economic_debt)}</td>
                    <td className="py-1.5 px-3 text-right font-mono text-slate-400">{r.flag_count ?? "—"}</td>
                    <td className="py-1.5 px-3 text-right font-mono text-slate-400">{fmt0(r.liquidity_tone)}</td>
                    <td className="py-1.5 px-3 text-right font-mono text-slate-300">
                      {r.overall_risk == null ? "—" : `${r.overall_risk.toFixed(1)}${r.implied_rating ? ` · ${r.implied_rating}` : ""}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-2 text-[11px] text-slate-500">
            One row per analyzed company (latest snapshot) — click through for the full workup.
          </div>
        </>
      )}
    </div>
  );
}
