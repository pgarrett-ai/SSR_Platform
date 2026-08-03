import React from "react";
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import CitedNumber from "./CitedNumber.jsx";
import { ACCENT, Badge, INK, Td, Th, chartTooltipStyle, rowClass } from "../ui/index.jsx";

// Covenant packages per agreement family: a coverage strip mapping every debt instrument
// to its governing agreement, then one card per family — financial covenants, baskets,
// blockers, creditors, the anchor clause (always visible), and structured LME vectors
// (each tied to the covenant facts it rests on; silent vectors are not rendered) —
// closing with the covenant DOLLARS: the RP-basket capacity build and the
// permitted-liens headroom tables (formerly the separate "Covenant dollars" section).

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

// ---- Covenant dollars (merged from the former CovenantDollars component) -----------
// The RP-basket builder — how much value can leak to shareholders — and permitted-liens
// headroom — how much new secured debt can prime you. Renders from the cached overview
// prop, zero fetch; stale caches degrade to a re-run note. The qualitative J.Crew /
// uptier chips live on the family cards above — only the dollar builds render here.

const ARCH_TONE = {
  unbounded: "high", ratio_only: "watch", stated_capacity: "watch",
  computed: "ok", present_unquantified: "neutral", none_extracted: "neutral",
};

function Rerun({ what }) {
  return (
    <div className="text-xs text-slate-500">
      {what} not in this cached overview — re-run the pipeline (Run live) to extract.
    </div>
  );
}

// Provenance marker for a raw extracted string: § pops the verbatim covenant quote.
function QuoteMark({ quote, section }) {
  if (!quote) return null;
  return <CitedNumber cv={{ display: "§", citation: { quote, section } }} className="text-accent" />;
}

function RpHalf({ rp }) {
  if (!rp) return <Rerun what="RP basket" />;
  if (!rp.available)
    return <div className="text-xs text-slate-500">{(rp.notes || []).join(" · ")}</div>;
  const data = (rp.points || []).map((p) => ({ label: p.label, cumulative: p.cumulative }));
  return (
    <div>
      {rp.covenant_status === "none" && (
        <div className="mb-2 rounded-md border border-rose-500/40 bg-rose-500/10 p-2 text-xs text-rose-200">
          No RP covenant extracted — distributions contractually unrestricted (unbounded
          leakage). Builder shown for scale only.
        </div>
      )}
      <div className="mb-2 text-sm text-slate-200">
        RP capacity <CitedNumber cv={rp.capacity} className="text-slate-100" />
        <span className="ml-3 text-xs text-slate-500">
          starter <CitedNumber cv={rp.starter} className="text-slate-300" />
          {rp.covenant_status !== "extracted" && (
            <Badge tone="watch" className="ml-2"
              title="starter basket not extracted — builder formula only">
              builder-only
            </Badge>
          )}
        </span>
      </div>
      {data.length > 0 && (
        <ResponsiveContainer width="100%" height={120}>
          <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
            <CartesianGrid stroke={INK[600]} strokeDasharray="3 3" />
            <XAxis dataKey="label" tick={{ fill: "#94a3b8", fontSize: 9 }} />
            <YAxis tick={{ fill: "#94a3b8", fontSize: 9 }} />
            <Tooltip contentStyle={chartTooltipStyle}
              formatter={(v) => [`$${v}M cumulative`, null]} />
            <Line dataKey="cumulative" stroke={ACCENT} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      )}
      <details className="mt-1" open={rp.covenant_status !== "none"}>
        <summary className="cursor-pointer text-[11px] text-slate-500">
          quarterly build ($mm)
        </summary>
        <div className="overflow-x-auto">
          <table className="mt-1 w-full text-xs">
            <thead>
              <tr className="border-b border-ink-600">
                <Th>Quarter</Th><Th right>NI</Th>
                <Th right title="0.5×NI when positive, 1.0×NI when negative — losses deduct in full">NI credit</Th>
                <Th right>Equity</Th><Th right>Divs</Th><Th right>Buybacks</Th>
                <Th right>Contribution</Th><Th right>Cumulative</Th>
              </tr>
            </thead>
            <tbody>
              {(rp.points || []).map((p) => (
                <tr key={p.period_end} className="border-b border-ink-800 font-mono text-slate-300">
                  <td className="px-2 py-1 font-sans">{p.label}</td>
                  <td className="px-2 py-1 text-right">{p.net_income == null ? "—" : p.net_income.toLocaleString()}</td>
                  <td className="px-2 py-1 text-right">{p.ni_credit.toLocaleString()}</td>
                  <td className="px-2 py-1 text-right">{p.equity_proceeds.toLocaleString()}</td>
                  <td className="px-2 py-1 text-right">{p.dividends.toLocaleString()}</td>
                  <td className="px-2 py-1 text-right">{p.buybacks.toLocaleString()}</td>
                  <td className="px-2 py-1 text-right">{p.contribution.toLocaleString()}</td>
                  <td className="px-2 py-1 text-right text-slate-100">{p.cumulative.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      <div className="mt-2 space-y-0.5 text-[11px] text-slate-500">
        {rp.covenant_family && (
          <div>
            covenant layer: {rp.covenant_family} — {rp.covenant_value || "—"}
            {" "}<QuoteMark quote={rp.covenant_quote} section={rp.covenant_family} />
          </div>
        )}
        {rp.formula_note && <div>⚠ {rp.formula_note}</div>}
        {(rp.notes || []).map((n, i) => (<div key={i}>⚠ {n}</div>))}
      </div>
    </div>
  );
}

function LiensHalf({ lh }) {
  if (!lh) return <Rerun what="Liens headroom" />;
  if (!lh.available) return <div className="text-xs text-slate-500">{lh.note}</div>;
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        <Badge tone={ARCH_TONE[lh.archetype] || "neutral"}>
          {(lh.archetype || "").replace(/_/g, " ")}
        </Badge>
      </div>
      {lh.unbounded_note && (
        <div className="mb-2 rounded-md border border-rose-500/40 bg-rose-500/10 p-2 text-xs text-rose-200">
          {lh.unbounded_note}
        </div>
      )}
      {(lh.rows || []).length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-ink-600">
                <Th>Family</Th><Th>Lien fact</Th><Th>Extracted value</Th>
                <Th right>Headroom</Th>
              </tr>
            </thead>
            <tbody>
              {lh.rows.map((r, i) => (
                <tr key={i} className="border-b border-ink-800 align-top text-slate-300">
                  <td className="max-w-[10rem] truncate px-2 py-1 text-slate-400" title={r.family}>{r.family}</td>
                  <td className="max-w-[12rem] truncate px-2 py-1" title={r.name}>{r.name}</td>
                  <td className="max-w-[16rem] px-2 py-1 text-slate-400">
                    <span className="line-clamp-2" title={r.value}>{r.value || "—"}</span>
                    {" "}<QuoteMark quote={r.quote} section={`${r.family} — ${r.name}`} />
                  </td>
                  <td className="px-2 py-1 text-right">
                    {r.headroom
                      ? <CitedNumber cv={r.headroom} className="text-slate-100" />
                      : <span className="text-[11px] text-slate-500" title={r.detail}>
                          {(r.archetype || "").replace(/_/g, " ")}
                        </span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="mt-2 space-y-0.5 text-[11px] text-slate-500">
        {lh.suggested_priming && (
          <div title={lh.suggested_priming.note}>
            priming pre-seed: <span className="font-mono text-slate-300">
              ${Math.round(lh.suggested_priming.value).toLocaleString()}M</span>
            {" "}— {lh.suggested_priming.basis} (Recovery page → Priming scenario)
          </div>
        )}
        <div>{lh.derivation}</div>
      </div>
    </div>
  );
}

function CovenantDollarsGrid({ overview }) {
  return (
    <div className="grid gap-6 md:grid-cols-2">
      <div>
        <div className="mb-2 text-[10px] uppercase tracking-wide text-slate-500"
          title="capacity(t) = starter + Σ quarterly [0.5×NI if NI>0 else 1.0×NI + equity issuance − dividends − buybacks]">
          Restricted-payments basket
        </div>
        <RpHalf rp={overview?.rp_basket} />
      </div>
      <div>
        <div className="mb-2 text-[10px] uppercase tracking-wide text-slate-500"
          title="what an extracted lien covenant actually permits — ratio tests need appraisals, $ baskets are stated capacity, no covenant at all is unbounded priming risk">
          Permitted-liens headroom
        </div>
        <LiensHalf lh={overview?.liens_headroom} />
      </div>
    </div>
  );
}

export default function CovenantPackages({ covenants, instruments, overview }) {
  // coverage strip: which agreement governs each instrument in the debt schedule
  const governedBy = {};
  for (const c of covenants || []) {
    for (const n of c.governs_instruments || []) governedBy[n] = c.family_label;
  }
  const strip = (instruments || []).map((d) => ({
    name: d.instrument,
    family: d.governed_by || governedBy[d.instrument] || null,
  }));
  const matched = strip.filter((s) => s.family);

  return (
    <div className="grid gap-4">
      {(!covenants || covenants.length === 0) && (
        <p className="text-sm text-slate-400">
          No covenant terms extracted — requires LLM extraction (EX-10.x / EX-4.x).
        </p>
      )}
      {covenants?.length > 0 && strip.length > 0 && (
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
      {(covenants || []).map((c, i) => (
        <PackageCard key={i} cov={c} />
      ))}
      <CovenantDollarsGrid overview={overview} />
    </div>
  );
}
