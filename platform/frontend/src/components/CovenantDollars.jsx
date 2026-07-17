import React from "react";
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import CitedNumber from "./CitedNumber.jsx";
import { ACCENT, Badge, INK, Td, Th, chartTooltipStyle } from "../ui/index.jsx";

// Covenant dollars (Moyer ch. 7/9): the RP-basket builder — how much value can leak to
// shareholders — and permitted-liens headroom — how much new secured debt can prime you.
// Renders from the cached overview prop, zero fetch; stale caches degrade to a re-run note.

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
          leakage, Moyer ch. 9). Builder shown for scale only.
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
                <Th right title="0.5×NI when positive, 1.0×NI when negative — losses deduct in full (Moyer ch. 7)">NI credit</Th>
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
        {lh.uptier_priming && (
          <Badge tone={lh.uptier_priming.risk === "open" ? "high" : "watch"}
            title={`${lh.uptier_priming.family}: ${lh.uptier_priming.rationale || ""}`}>
            uptier {lh.uptier_priming.risk}
          </Badge>
        )}
        {lh.j_crew_blocker_present != null && (
          <Badge tone={lh.j_crew_blocker_present ? "ok" : "high"}
            title="unrestricted-subsidiary asset-transfer blocker (dropdown protection)">
            J.Crew blocker {lh.j_crew_blocker_present ? "present" : "absent"}
          </Badge>
        )}
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

export default function CovenantDollars({ overview }) {
  return (
    <div className="grid gap-6 md:grid-cols-2">
      <div>
        <div className="mb-2 text-[10px] uppercase tracking-wide text-slate-500"
          title="capacity(t) = starter + Σ quarterly [0.5×NI if NI>0 else 1.0×NI + equity issuance − dividends − buybacks] (Moyer ch. 7)">
          Restricted-payments basket (ch. 7)
        </div>
        <RpHalf rp={overview?.rp_basket} />
      </div>
      <div>
        <div className="mb-2 text-[10px] uppercase tracking-wide text-slate-500"
          title="what an extracted lien covenant actually permits — ratio tests need appraisals, $ baskets are stated capacity, no covenant at all is unbounded priming risk (Moyer ch. 9)">
          Permitted-liens headroom (ch. 9)
        </div>
        <LiensHalf lh={overview?.liens_headroom} />
      </div>
    </div>
  );
}
