import React from "react";
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import CitedNumber from "./CitedNumber.jsx";
import { ACCENT, Badge, INK, Td, Th, chartTooltipStyle, fmtMoney, rowClass } from "../ui/index.jsx";

// What the documents let the company do to its creditors. One collapsed card per
// agreement family whose summary row carries the LME read (uptier, drop-down,
// incremental, RP leakage), so the cross-family scan needs no clicks; the card body
// holds the terms it rests on. Covenant dollars (liens headroom, RP capacity) render
// only when the extraction actually priced them.

function Row({ label, value, mono }) {
  if (value == null || value === "") return null;
  return (
    <div className="flex flex-col gap-0.5 border-b border-ink-700/50 py-2 sm:flex-row sm:gap-3">
      <span className="w-48 shrink-0 text-[11px] uppercase tracking-wide text-slate-400">{label}</span>
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

const VECTORS = [
  ["uptier_priming", "Uptier / priming", "uptier"],
  ["dropdown_jcrew", "Drop-down (J.Crew)", "drop-down"],
  ["incremental_debt", "Incremental debt", "incremental"],
  ["rp_leakage", "RP leakage", "RP leakage"],
];
const RISK_TONE = { protected: "ok", open: "high", unclear: "watch" };

// Vectors the documents speak to, in a fixed order so rows line up across families.
const readVectors = (cov) =>
  VECTORS.map(([key, label, short]) => {
    const v = (cov.lme_vectors || []).find((x) => x.vector === key);
    return v && v.risk && v.risk !== "not_addressed" ? { ...v, label, short } : null;
  }).filter(Boolean);

function PackageCard({ cov }) {
  // agreement_type is a raw enum ("credit_agreement") — humanize for display
  const raw = cov.family_label || cov.agreement_type || "Credit agreement";
  const title = raw.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()).replace(/^Dip\b/, "DIP");
  const vectors = readVectors(cov);
  // the summary row carries decided reads only; undetermined ones collapse to one count
  const decided = vectors.filter((v) => v.risk !== "unclear");
  const unclear = vectors.filter((v) => v.risk === "unclear");
  const creditors = [
    cov.admin_agent && `Admin agent: ${cov.admin_agent}`,
    cov.trustee && `Trustee: ${cov.trustee}`,
    cov.collateral_agent && `Collateral agent: ${cov.collateral_agent}`,
  ].filter(Boolean);

  return (
    <details className="group rounded-lg border border-ink-700 bg-ink-800/50">
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2 [&::-webkit-details-marker]:hidden">
        <span aria-hidden className="text-[11px] text-slate-500">
          <span className="group-open:hidden">▸</span><span className="hidden group-open:inline">▾</span>
        </span>
        <span className="text-sm font-semibold text-slate-100">{title}</span>
        {(cov.governs_instruments || []).map((n, i) => (
          <span key={i} className="rounded bg-accent/10 px-1.5 py-0.5 text-[11px] text-accent">{n}</span>
        ))}
        {vectors.length > 0 && (
          <span className="ml-auto flex flex-wrap gap-1">
            {decided.map((v) => (
              <Badge key={v.vector} tone={RISK_TONE[v.risk] || "neutral"}
                title={[v.rationale, v.basis && `basis: ${v.basis}`].filter(Boolean).join(" — ") || undefined}>
                {v.short} {v.risk}
              </Badge>
            ))}
            {unclear.length > 0 && (
              <Badge title={`not determinable from the documents on file: ${unclear.map((v) => v.short).join(", ")}`}>
                {unclear.length} unclear
              </Badge>
            )}
          </span>
        )}
      </summary>

      <div className="border-t border-ink-700 px-4 pb-4 pt-2">
        <div className="mb-1 flex flex-wrap gap-2">
          {cov.amendment_count > 0 && (
            <Badge>{cov.amendment_count} amendment{cov.amendment_count === 1 ? "" : "s"}</Badge>
          )}
          {cov.base_missing && (
            <Badge tone="watch" title="only amendments are on file in the lookback window, so the base terms were not read">
              base agreement not on file
            </Badge>
          )}
          {cov.citation?.exhibit && <Badge mono>{cov.citation.exhibit}</Badge>}
        </div>

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

        {(cov.baskets || []).map((b, i) => <Row key={i} label={b.name} value={b.value} />)}
        <Row label="Restricted-payments basket" value={cov.restricted_payments_basket_size} />
        <Row label="MFN sunset" value={cov.mfn_sunset_period} />
        <Row label="Unrestricted-sub flexibility" value={cov.unrestricted_subsidiary_designation_flexibility} />
        <div className="flex flex-col gap-0.5 border-b border-ink-700/50 py-2 sm:flex-row sm:gap-3">
          <span className="w-48 shrink-0 text-[11px] uppercase tracking-wide text-slate-400">J.Crew blocker</span>
          <Blocker present={cov.j_crew_blocker_present} />
        </div>
        {vectors.map((v) => <Row key={v.vector} label={v.label} value={v.rationale} />)}
        {vectors.length === 0 && <Row label="LME read" value={cov.lme_risk_notes} />}

        {creditors.length > 0 && (
          <div className="border-b border-ink-700/50 py-2">
            <span className="text-[11px] uppercase tracking-wide text-slate-400">Agents</span>
            <div className="mt-1 text-[13px] text-slate-200">{creditors.join(" · ")}</div>
            {cov.creditor_note && <div className="mt-0.5 text-[11px] text-slate-400">{cov.creditor_note}</div>}
          </div>
        )}

        {(cov.anchor_clause || cov.citation?.quote) && (
          <blockquote className="mt-3 border-l-2 border-accent/50 pl-3 text-[12px] italic text-slate-300">
            “{cov.anchor_clause || cov.citation.quote}”
            {cov.citation?.source_url && (
              <a href={cov.citation.source_url} target="_blank" rel="noreferrer"
                className="ml-2 not-italic text-accent hover:underline">
                ↗ source
              </a>
            )}
          </blockquote>
        )}
      </div>
    </details>
  );
}

// Provenance marker for a raw extracted string: § pops the verbatim covenant quote.
function QuoteMark({ quote, section }) {
  if (!quote) return null;
  return <CitedNumber cv={{ display: "§", citation: { quote, section } }} className="text-accent" />;
}

const LIENS_READ = {
  unbounded: "No lien covenant in the documents on file — unbounded priming risk.",
  ratio_only: "Liens are capped by ratio tests only — headroom needs an asset appraisal.",
  stated_capacity: "Lien capacity is stated as dollar baskets; utilization is not disclosed.",
  computed: "Lien headroom is computed from the covenant tests below.",
  present_unquantified: "Lien covenants are present but not quantified.",
  none_extracted: "No lien terms found in the documents on file.",
};

function LiensRead({ lh }) {
  if (!lh?.available) return null;
  const text = lh.unbounded_note || LIENS_READ[lh.archetype];
  if (!text) return null;
  return (
    <p className={`mb-3 text-[13px] ${lh.archetype === "unbounded" ? "text-rose-300" : "text-slate-300"}`}>
      <span className="mr-2 text-[11px] uppercase tracking-wide text-slate-400">Liens</span>{text}
    </p>
  );
}

function LiensHeadroom({ lh }) {
  const rows = (lh?.available && lh.rows || []).filter((r) => r.headroom);
  if (!rows.length) return null;
  return (
    <div className="mt-4 overflow-x-auto">
      <div className="mb-1 text-[11px] uppercase tracking-wide text-slate-400">Permitted-liens headroom</div>
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-ink-600">
            <Th>Family</Th><Th>Lien term</Th><Th>As written</Th><Th right>Headroom</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-ink-700/60 align-top text-slate-300">
              <td className="max-w-[10rem] truncate px-2 py-1 text-slate-400" title={r.family}>{r.family}</td>
              <td className="max-w-[12rem] truncate px-2 py-1" title={r.name}>{r.name}</td>
              <td className="max-w-[16rem] px-2 py-1 text-slate-400">
                <span className="line-clamp-2" title={r.value}>{r.value || "—"}</span>
                {" "}<QuoteMark quote={r.quote} section={`${r.family} — ${r.name}`} />
              </td>
              <td className="px-2 py-1 text-right"><CitedNumber cv={r.headroom} className="text-slate-100" /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// capacity(t) = starter + Σ quarterly [0.5×NI if NI > 0 else 1.0×NI + equity issuance −
// dividends − buybacks]; shown only for an extracted, non-zero basket.
function RpBasket({ rp }) {
  if (rp?.covenant_status !== "extracted" || !(rp.capacity?.value > 0)) return null;
  const data = (rp.points || []).map((p) => ({ label: p.label, cumulative: p.cumulative }));
  return (
    <div className="mt-4">
      <div className="mb-1 text-[11px] uppercase tracking-wide text-slate-400">Restricted-payments capacity</div>
      <div className="mb-2 text-sm text-slate-200">
        <CitedNumber cv={rp.capacity} className="text-slate-100" />
        <span className="ml-3 text-xs text-slate-400">
          starter <CitedNumber cv={rp.starter} className="text-slate-300" />
          {rp.covenant_family && (
            <> · {rp.covenant_family} <QuoteMark quote={rp.covenant_quote} section={rp.covenant_family} /></>
          )}
        </span>
      </div>
      {data.length > 1 && (
        <ResponsiveContainer width="100%" height={120}>
          <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
            <CartesianGrid stroke={INK[600]} strokeDasharray="3 3" />
            <XAxis dataKey="label" tick={{ fill: "#94a3b8", fontSize: 11 }} />
            <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} />
            <Tooltip contentStyle={chartTooltipStyle} formatter={(v) => [`${fmtMoney(v * 1e6)} cumulative`, null]} />
            <Line dataKey="cumulative" stroke={ACCENT} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      )}
      {(rp.notes || []).map((n, i) => <p key={i} className="mt-1 text-[11px] text-slate-400">{n}</p>)}
    </div>
  );
}

export default function CovenantPackages({ covenants, liens, rp }) {
  return (
    <div>
      <LiensRead lh={liens} />
      {covenants?.length > 0 ? (
        <div className="space-y-2">
          {covenants.map((c, i) => <PackageCard key={i} cov={c} />)}
        </div>
      ) : (
        <p className="text-sm text-slate-400">No covenant terms found in the filed credit documents.</p>
      )}
      <LiensHeadroom lh={liens} />
      <RpBasket rp={rp} />
    </div>
  );
}
