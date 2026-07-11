import React, { useEffect, useState } from "react";
import { fetchMdnaPeriods, fetchMdnaText } from "../api.js";
import { getCached, setCached, useAsync } from "../cache.js";

// The actual MD&A, per filing period: tabs newest-first (10-K/10-Q + period end), a reading
// pane, and the EDGAR source link. Text comes from the mdna_sections store — populated by
// any overview run for the ticker.

function tabLabel(p) {
  const period = p.period_end ? String(p.period_end).slice(0, 10) : p.accession_no;
  return `${p.form_type || "filing"} · ${period}`;
}

export default function MdnaReader({ ticker }) {
  const { data: periods, error, loading } = useAsync(
    `mdna:${ticker}`, () => fetchMdnaPeriods(ticker), [ticker]);
  const [active, setActive] = useState(null);
  const [doc, setDoc] = useState(null);
  const [docError, setDocError] = useState(null);

  const activeAccession = active ?? periods?.[0]?.accession_no ?? null;

  useEffect(() => { setActive(null); }, [ticker]);

  useEffect(() => {
    if (!activeAccession) return;
    const key = `mdna:${ticker}:${activeAccession}`;
    const cached = getCached(key);
    if (cached) {
      setDoc(cached);
      setDocError(null);
      return;
    }
    let alive = true;
    setDoc(null);
    setDocError(null);
    fetchMdnaText(ticker, activeAccession)
      .then((d) => alive && setDoc(setCached(key, d)))
      .catch((e) => alive && setDocError(e.message));
    return () => { alive = false; };
  }, [ticker, activeAccession]);

  if (loading) return <div className="text-sm text-slate-500">Loading stored MD&A…</div>;
  if (error) return <div className="text-sm text-rose-300">{error}</div>;
  if (!periods || periods.length === 0) {
    return (
      <div className="text-sm text-slate-500">
        No stored MD&A yet — run the pipeline for this ticker once to populate it.
      </div>
    );
  }

  const activePeriod = periods.find((p) => p.accession_no === activeAccession);

  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-1 border-b border-ink-700">
        {periods.map((p) => (
          <button
            key={p.accession_no}
            onClick={() => setActive(p.accession_no)}
            className={`px-3 py-1.5 font-mono text-[11px] ${
              p.accession_no === activeAccession
                ? "border-b-2 border-accent font-semibold text-white"
                : "text-slate-500 hover:text-slate-300"
            }`}
          >
            {tabLabel(p)}
          </button>
        ))}
      </div>
      {activePeriod?.source_url && (
        <a
          href={activePeriod.source_url}
          target="_blank"
          rel="noreferrer"
          className="mb-2 block font-mono text-[11px] text-accent hover:underline"
        >
          ↗ Open filing on SEC.gov
        </a>
      )}
      {docError && <div className="text-sm text-rose-300">{docError}</div>}
      {!doc && !docError && <div className="text-sm text-slate-500">Loading…</div>}
      {doc && (
        <div className="max-h-[32rem] overflow-y-auto whitespace-pre-wrap rounded-md bg-ink-900/60 p-4 text-[13px] leading-relaxed text-slate-300">
          {doc.text}
        </div>
      )}
    </div>
  );
}
