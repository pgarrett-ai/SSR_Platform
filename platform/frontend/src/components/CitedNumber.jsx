import React, { useState } from "react";

// Renders a single value with its provenance. Hovering shows the citation (verbatim quote +
// link to the EDGAR source) or, for a derived figure, the formula. Nothing is shown uncited.
export default function CitedNumber({ cv, className = "", placeholder = "—" }) {
  const [open, setOpen] = useState(false);
  if (!cv || (cv.value == null && !cv.display)) {
    return <span className="text-slate-600">{placeholder}</span>;
  }

  const label = cv.display ?? (cv.value != null ? String(cv.value) : placeholder);
  const hasCite = !!cv.citation;
  const isDerived = cv.derived;
  const tie = cv.tie_out;
  const tieColor = tie?.status === "match" ? "text-emerald-400" : tie?.status === "mismatch" ? "text-amber-400" : "text-slate-500";

  return (
    <span
      className="relative inline-block"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <span
        className={`cite-link font-mono tabular-nums ${className} ${
          isDerived ? "decoration-amber-400/50" : ""
        }`}
      >
        {label}
        {isDerived && <sup className="ml-0.5 text-[9px] text-amber-400/80">ƒ</sup>}
      </span>
      {tie && tie.status !== "no_xbrl" && (
        <sup className={`ml-1 text-[9px] font-semibold ${tieColor}`} title="XBRL tie-out">
          {tie.status === "match" ? "✓XBRL" : `⚠${tie.delta_pct > 0 ? "+" : ""}${tie.delta_pct}%`}
        </sup>
      )}

      {open && (
        <span className="absolute z-30 left-1/2 top-full mt-1 w-80 -translate-x-1/2 rounded-md border border-ink-600 bg-ink-800 p-3 text-left shadow-xl">
          {hasCite ? (
            <span className="block text-xs">
              <span className="mb-1 flex items-center gap-2 text-[10px] uppercase tracking-wide text-slate-400">
                <span className="rounded bg-ink-600 px-1.5 py-0.5 text-slate-200">
                  {cv.citation.form_type || "filing"}
                </span>
                <span>{cv.citation.filing_date}</span>
                {cv.citation.exhibit && <span>· {cv.citation.exhibit}</span>}
              </span>
              {cv.citation.section && (
                <span className="block text-[11px] text-slate-400">{cv.citation.section}</span>
              )}
              {cv.citation.quote && (
                <span className="mt-1 block border-l-2 border-accent/60 pl-2 text-[12px] italic text-slate-200">
                  “{cv.citation.quote}”
                </span>
              )}
              {cv.citation.source_url && (
                <a
                  href={cv.citation.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-2 block truncate text-[11px] text-accent hover:underline"
                >
                  ↗ Open source on SEC.gov
                </a>
              )}
            </span>
          ) : (
            <span className="block text-xs">
              <span className="mb-1 inline-block rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-amber-300">
                Derived
              </span>
              {cv.formula && (
                <span className="block font-mono text-[11px] text-slate-200">{cv.formula}</span>
              )}
              {cv.note && <span className="mt-1 block text-[11px] text-slate-400">{cv.note}</span>}
            </span>
          )}
          {tie && tie.status !== "no_xbrl" && (
            <span className="mt-2 block border-t border-ink-600 pt-2 text-[11px]">
              <span className={`font-semibold ${tieColor}`}>
                {tie.status === "match" ? "✓ Ties out to XBRL" : `⚠ ${tie.delta_pct > 0 ? "+" : ""}${tie.delta_pct}% vs XBRL`}
              </span>
              <span className="mt-0.5 block text-slate-400">
                footnote {tie.llm_display} vs XBRL {tie.xbrl_display}
                {tie.xbrl_concept ? ` · ${tie.xbrl_concept}` : ""}
              </span>
            </span>
          )}
        </span>
      )}
    </span>
  );
}
