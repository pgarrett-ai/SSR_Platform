import React from "react";
import { Badge, Td, Th, rowClass } from "../ui/index.jsx";

// Covenant packages per agreement family: a coverage strip mapping every debt instrument
// to its governing agreement, then one card per family — financial covenants, baskets,
// blockers, creditors, the anchor clause (always visible), and structured LME vectors
// (each tied to the covenant facts it rests on; silent vectors are not rendered).

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
  if (present == null) return <span className="text-[13px] text-slate-500">not stated</span>;
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

const VECTOR_LABELS = {
  uptier_priming: "Uptier / priming",
  dropdown_jcrew: "Drop-down (J.Crew)",
  incremental_debt: "Incremental debt",
  rp_leakage: "RP leakage",
};

const RISK_TONE = { protected: "ok", open: "high", unclear: "watch" };

function LmeVectors({ vectors }) {
  const shown = (vectors || []).filter((v) => v.risk && v.risk !== "not_addressed");
  if (shown.length === 0) return null;
  return (
    <div className="mt-3 rounded-md border border-amber-500/30 bg-amber-500/5 p-3">
      <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-amber-200/90">
        LME vulnerability read — contractual capacity, not an event
      </div>
      <div className="space-y-2">
        {shown.map((v, i) => (
          <div key={i} className="text-[12px] text-slate-300">
            <Badge tone={RISK_TONE[v.risk] || "neutral"} className="mr-2">
              {v.risk}
            </Badge>
            <span className="font-semibold text-slate-200">{VECTOR_LABELS[v.vector] || v.vector}</span>
            {v.rationale && <span className="text-slate-400"> — {v.rationale}</span>}
            {v.basis && (
              <div className="mt-0.5 text-[11px] text-slate-500">basis: {v.basis}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function PackageCard({ cov }) {
  // agreement_type is a raw enum ("credit_agreement") — humanize for display
  const raw = cov.family_label || cov.agreement_type || "Credit agreement";
  const title = raw.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
  const creditors = [
    cov.admin_agent && `Admin agent: ${cov.admin_agent}`,
    cov.trustee && `Trustee: ${cov.trustee}`,
    cov.collateral_agent && `Collateral agent: ${cov.collateral_agent}`,
  ].filter(Boolean);

  return (
    <div className="rounded-xl border border-ink-700 bg-ink-800/50 p-4">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-slate-100">{title}</h4>
        <div className="flex items-center gap-2">
          {cov.amendment_count > 0 && (
            <Badge tone="neutral">{cov.amendment_count} amendment{cov.amendment_count === 1 ? "" : "s"}</Badge>
          )}
          {cov.base_missing && (
            <Badge tone="watch" className="cursor-help"
              title="only amendments are on file in the lookback window — base terms not read">
              base not on file
            </Badge>
          )}
          {cov.citation?.exhibit && <Badge mono>{cov.citation.exhibit}</Badge>}
        </div>
      </div>

      {cov.governs_instruments?.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {cov.governs_instruments.map((n, i) => (
            <span key={i} className="rounded bg-accent/10 px-2 py-0.5 text-[11px] text-accent">
              {n}
            </span>
          ))}
        </div>
      )}

      {cov.financial_covenants?.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="mb-1 w-full text-sm">
            <thead>
              <tr className="border-b border-ink-600">
                <Th>Financial covenant</Th>
                <Th>Threshold</Th>
                <Th>Tested</Th>
              </tr>
            </thead>
            <tbody>
              {cov.financial_covenants.map((fc, i) => (
                <tr key={i} className={rowClass}>
                  <Td className="text-slate-200">{fc.kind || "—"}</Td>
                  <Td mono className="text-[12px] text-slate-300">{fc.threshold || "—"}</Td>
                  <Td className="text-[12px] text-slate-400">
                    {[fc.test_frequency, fc.springing_trigger].filter(Boolean).join(" · ") || "—"}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <>
          <Row label="Financial covenant" value={cov.leverage_covenant_type} />
          <Row label="Threshold" value={cov.leverage_ratio_threshold} mono />
        </>
      )}

      {cov.baskets?.length > 0 &&
        cov.baskets.map((b, i) => <Row key={i} label={b.name} value={b.value} />)}
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

      {creditors.length > 0 && (
        <div className="border-b border-ink-700/50 py-2">
          <span className="text-[12px] uppercase tracking-wide text-slate-500">Creditors</span>
          <div className="mt-1 text-[13px] text-slate-200">{creditors.join(" · ")}</div>
          {cov.creditor_note && (
            <div className="mt-0.5 text-[11px] text-slate-500">{cov.creditor_note}</div>
          )}
        </div>
      )}

      {(cov.anchor_clause || cov.citation?.quote) && (
        <blockquote className="mt-3 border-l-2 border-accent/50 pl-3 text-[12px] italic text-slate-300">
          “{cov.anchor_clause || cov.citation.quote}”
          {cov.citation?.source_url && (
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

      {cov.lme_vectors?.length > 0 ? (
        <LmeVectors vectors={cov.lme_vectors} />
      ) : (
        cov.lme_risk_notes && (
          <div className="mt-2 rounded-md border border-amber-500/30 bg-amber-500/5 p-2 text-[12px] text-amber-200/90">
            <span className="font-semibold">LME assessment:</span> {cov.lme_risk_notes}
          </div>
        )
      )}
    </div>
  );
}

export default function CovenantPackages({ covenants, instruments }) {
  if (!covenants || covenants.length === 0) {
    return (
      <p className="text-sm text-slate-400">
        No covenant terms extracted — requires LLM extraction (EX-10.x / EX-4.x).
      </p>
    );
  }

  // coverage strip: which agreement governs each instrument in the debt schedule
  const governedBy = {};
  for (const c of covenants) {
    for (const n of c.governs_instruments || []) governedBy[n] = c.family_label;
  }
  const strip = (instruments || []).map((d) => ({
    name: d.instrument,
    family: d.governed_by || governedBy[d.instrument] || null,
  }));
  const matched = strip.filter((s) => s.family);

  return (
    <div className="grid gap-4">
      {strip.length > 0 && (
        <div className="rounded-xl border border-ink-700 bg-ink-900/50 p-3">
          <div className="text-[11px] uppercase tracking-wide text-slate-500">
            Coverage — {matched.length} of {strip.length} instruments matched to an agreement on file
          </div>
          {matched.length > 0 && (
            <div className="mt-2 grid gap-x-6 gap-y-1 text-[12px] sm:grid-cols-2">
              {matched.map((s, i) => (
                <div key={i} className="flex justify-between gap-2">
                  <span className="truncate text-slate-300">{s.name}</span>
                  <span className="shrink-0 text-accent">{s.family}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      {covenants.map((c, i) => (
        <PackageCard key={i} cov={c} />
      ))}
    </div>
  );
}
