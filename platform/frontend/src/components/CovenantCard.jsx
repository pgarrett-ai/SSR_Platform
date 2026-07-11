import React, { useState } from "react";

function Row({ label, value, mono }) {
  if (value == null || value === "") return null;
  return (
    <div className="flex flex-col gap-0.5 border-b border-ink-700/50 py-2 sm:flex-row sm:gap-3">
      <span className="w-60 shrink-0 text-[12px] uppercase tracking-wide text-slate-500">
        {label}
      </span>
      <span className={`text-[13px] text-slate-200 ${mono ? "font-mono" : ""}`}>{value}</span>
    </div>
  );
}

function Blocker({ present }) {
  if (present == null) return <span className="text-slate-500">not stated</span>;
  return present ? (
    <span className="rounded bg-emerald-500/15 px-2 py-0.5 text-[11px] text-emerald-300">
      present — IP/asset transfer restricted
    </span>
  ) : (
    <span className="rounded bg-rose-500/15 px-2 py-0.5 text-[11px] text-rose-300">
      absent — trapdoor risk
    </span>
  );
}

function CovenantBlock({ cov }) {
  const [showQuote, setShowQuote] = useState(false);
  return (
    <div className="rounded-xl border border-ink-700 bg-ink-800/50 p-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-slate-100">
          {cov.agreement_type || "Credit agreement"}
        </h4>
        {cov.citation?.exhibit && (
          <span className="rounded bg-ink-600 px-2 py-0.5 font-mono text-[10px] text-slate-300">
            {cov.citation.exhibit}
          </span>
        )}
      </div>

      <Row label="Financial covenant" value={cov.leverage_covenant_type} />
      <Row label="Threshold" value={cov.leverage_ratio_threshold} mono />
      <Row label="Restricted-payments basket" value={cov.restricted_payments_basket_size} />
      <Row label="MFN sunset" value={cov.mfn_sunset_period} />
      <Row
        label="Unrestricted-sub flexibility"
        value={cov.unrestricted_subsidiary_designation_flexibility}
      />
      <div className="flex flex-col gap-0.5 border-b border-ink-700/50 py-2 sm:flex-row sm:gap-3">
        <span className="w-60 shrink-0 text-[12px] uppercase tracking-wide text-slate-500">
          J.Crew blocker
        </span>
        <Blocker present={cov.j_crew_blocker_present} />
      </div>
      {cov.lme_risk_notes && (
        <div className="mt-2 rounded-md border border-amber-500/30 bg-amber-500/5 p-2 text-[12px] text-amber-200/90">
          <span className="font-semibold">LME read:</span> {cov.lme_risk_notes}
        </div>
      )}
      {cov.citation?.quote && (
        <div className="mt-3">
          <button
            onClick={() => setShowQuote((v) => !v)}
            className="text-[11px] text-accent hover:underline"
          >
            {showQuote ? "Hide" : "Show"} anchoring clause
          </button>
          {showQuote && (
            <blockquote className="mt-2 border-l-2 border-accent/50 pl-3 text-[12px] italic text-slate-300">
              “{cov.citation.quote}”
              {cov.citation.source_url && (
                <a
                  href={cov.citation.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="ml-2 not-italic text-accent hover:underline"
                >
                  ↗ source
                </a>
              )}
            </blockquote>
          )}
        </div>
      )}
    </div>
  );
}

export default function CovenantCard({ covenants }) {
  if (!covenants || covenants.length === 0) {
    return (
      <p className="text-sm text-slate-400">
        No covenant terms extracted — requires LLM extraction (EX-10.x / EX-4.x).
      </p>
    );
  }
  return (
    <div className="grid gap-4">
      {covenants.map((c, i) => (
        <CovenantBlock key={i} cov={c} />
      ))}
    </div>
  );
}
