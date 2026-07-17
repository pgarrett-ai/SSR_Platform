import React, { useState } from "react";
import { fetchLadder } from "../api.js";
import { useAsync } from "../cache.js";
import CitedNumber from "./CitedNumber.jsx";
import { Badge, Loading, Td, Th } from "../ui/index.jsx";

// Moyer's creation-value test: what multiple of EBITDA are you creating the company at
// through each class — cumulative claims at face and at market (TRACE drop-file quotes;
// face fallback flagged "unquoted"). Fulcrum class highlighted like the Recovery grid.

const x = (v) => (v == null ? "—" : `${v.toFixed(2)}x`);

// F9 detector chip label: busted-convert ratio, PIK, or mezzanine.
const detectorLabel = (it) =>
  it.kind === "busted_convert"
    ? `convert ${it.ratio == null ? "?" : `${it.ratio.toFixed(2)}x`}`
    : it.kind === "pik" ? "PIK" : "mezzanine";

export default function CreationLadder({ ticker, years }) {
  const [recast, setRecast] = useState(false);   // mezzanine recast-as-debt toggle
  const key = recast ? `ladder:${ticker}:${years}:recast` : `ladder:${ticker}:${years}`;
  const { data, loading, error } = useAsync(
    key, () => fetchLadder(ticker, years, recast), [ticker, years, recast]);
  const [variant, setVariant] = useState("ltm");

  if (loading) return <Loading />;
  if (error) return <div className="text-xs text-rose-300">{error}</div>;
  if (!data?.classes?.length)
    return <div className="text-xs text-slate-500">No instruments in the debt schedule.</div>;

  const e = data.ebitda_mm?.[variant];
  const mult = (cum) => (e != null && e > 0 ? cum / e : null);
  const hasAdj = data.ebitda_mm?.covenant_adjusted != null;
  const detector = data.detector?.items || [];
  const hasMezz = detector.some((it) => it.kind === "mezzanine");

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-slate-400">
        {data.net_market_leverage && (
          <span>
            net-at-market leverage:{" "}
            <CitedNumber cv={data.net_market_leverage} className="text-slate-200" />
          </span>
        )}
        <span>
          quotes matched: <span className="font-mono text-slate-200">{data.n_quoted}/{data.n_instruments}</span>
          {data.quote_feed?.enabled === false && (
            <span className="ml-1 text-slate-600">({data.quote_feed.note})</span>
          )}
        </span>
        {hasAdj && (
          <label className="flex cursor-pointer items-center gap-1.5 text-slate-400">
            <input type="checkbox" checked={variant === "covenant_adjusted"}
              onChange={(ev) => setVariant(ev.target.checked ? "covenant_adjusted" : "ltm")} />
            covenant-adjusted EBITDA
          </label>
        )}
      </div>

      {detector.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-slate-400">
          <span className="text-[10px] uppercase tracking-wide text-slate-500"
            title="instruments engineered around leverage optics — busted converts, PIK, mezzanine (Moyer ch. 6)">
            Capacity avoidance:
          </span>
          {detector.map((it, i) => (
            <span key={i} className="flex items-center gap-1.5" title={it.note}>
              <Badge tone={it.tone || "neutral"}>{detectorLabel(it)}</Badge>
              <span className="max-w-[280px] truncate">{it.instrument}</span>
            </span>
          ))}
          {hasMezz && (
            <label className="flex cursor-pointer items-center gap-1.5 text-slate-400"
              title="append temporary equity as a preferred claim — pays after debt, before common">
              <input type="checkbox" checked={recast}
                onChange={(ev) => setRecast(ev.target.checked)} />
              recast mezzanine as debt
            </label>
          )}
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-ink-600">
              <Th>Class</Th>
              <Th right>Face $mm</Th>
              <Th right>Cum face</Th>
              {data.has_oid && (
                <Th right title="cumulative accreted (carrying) value — the claim; unamortized OID is not a claim (Moyer ch. 5)">
                  Cum @ accreted
                </Th>
              )}
              <Th right>Cum @ market</Th>
              <Th right title="cumulative face ÷ EBITDA">Creation x (face)</Th>
              <Th right title="cumulative market value ÷ EBITDA — the Moyer cheapness test">
                Creation x (mkt)
              </Th>
            </tr>
          </thead>
          <tbody>
            {data.classes.map((c) => (
              <tr key={c.label + c.cum_face}
                className={`border-b border-ink-800 ${c.is_fulcrum ? "bg-rose-500/5" : ""}`}>
                <Td className={c.is_fulcrum ? "text-rose-300" : "text-slate-300"}>
                  {c.label}
                  {c.is_fulcrum && <Badge tone="high" className="ml-2">fulcrum</Badge>}
                  <span className="ml-2 text-[10px] text-slate-600">
                    {c.members.length} instrument{c.members.length === 1 ? "" : "s"}
                  </span>
                </Td>
                <Td right mono className="text-slate-300">{c.face.toLocaleString()}</Td>
                <Td right mono className="text-slate-300">{c.cum_face.toLocaleString()}</Td>
                {data.has_oid && (
                  <Td right mono className="text-slate-300">{c.cum_accreted?.toLocaleString()}</Td>
                )}
                <Td right mono className="text-slate-200">
                  {c.cum_market.toLocaleString()}
                  {c.unquoted && (
                    <span className="ml-1.5 text-[9px] uppercase text-slate-600"
                      title="one or more instruments have no matched quote — carried at face">
                      unquoted
                    </span>
                  )}
                </Td>
                <Td right mono className="text-slate-300">{x(mult(c.cum_face))}</Td>
                <Td right mono className={c.is_fulcrum ? "text-rose-300" : "text-slate-100"}>
                  {x(mult(c.cum_market))}
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-2 space-y-0.5 text-[11px] text-slate-500">
        <div>{data.derivation}{e == null || e <= 0 ? " — EBITDA ≤ 0: multiples n.m." : ""}</div>
        {data.fulcrum_class && <div>{data.fulcrum_note}</div>}
        {data.has_oid && data.oid_note && <div>{data.oid_note}</div>}
        {data.detector?.meta_note && <div>⚠ {data.detector.meta_note}</div>}
        {(data.notes || []).map((n, i) => (<div key={i}>⚠ {n}</div>))}
      </div>
    </div>
  );
}
