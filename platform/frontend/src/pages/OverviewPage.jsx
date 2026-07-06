import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchHazard, fetchOverview, simulateRecovery } from "../api.js";
import { getCached, setCached } from "../cache.js";

// Company landing page: the four questions, one card each, loading independently.
//   How risky is it?      → hazard composite (shares the RiskPage cache)
//   How leveraged is it?  → capstack bridge, reported vs economic
//   What's the fulcrum / expected recovery? → quick fulcrum run on default assumptions
//   What should I read first? → forensic flags + pipeline warnings
// "What changed this quarter" needs historical snapshots — Phase 4.6.

const fmt = (v, d = 1) =>
  v == null || Number.isNaN(v) ? "–" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d });

function useAsync(key, loader, deps) {
  const [state, setState] = useState({ data: getCached(key) || null, error: null, loading: !getCached(key) });
  useEffect(() => {
    const cached = getCached(key);
    if (cached) {
      setState({ data: cached, error: null, loading: false });
      return;
    }
    let alive = true;
    setState({ data: null, error: null, loading: true });
    loader()
      .then((d) => alive && setState({ data: setCached(key, d), error: null, loading: false }))
      .catch((e) => alive && setState({ data: null, error: e.message, loading: false }));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

function Card({ title, to, toLabel, children, loading, error }) {
  return (
    <div className="flex flex-col rounded-xl border border-ink-700 bg-ink-800/50 p-4">
      <div className="mb-3 flex items-baseline justify-between">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{title}</div>
        {to && (
          <Link to={to} className="text-[11px] text-accent hover:underline">
            {toLabel || "open"} →
          </Link>
        )}
      </div>
      {loading && <div className="py-6 text-center text-xs text-slate-500">loading…</div>}
      {error && <div className="py-2 text-xs text-rose-300">{error}</div>}
      {!loading && !error && children}
    </div>
  );
}

const riskColor = (r) => (r == null ? "#64748b" : r >= 60 ? "#f87171" : r >= 35 ? "#fbbf24" : "#34d399");

export default function OverviewPage({ ticker, years }) {
  const ov = useAsync(`overview:${ticker}:${years}`, () => fetchOverview(ticker, years), [ticker, years]);
  const hz = useAsync(`hazard:${ticker}`, () => fetchHazard(ticker, 10), [ticker]);
  const rec = useAsync(`recovery-quick:${ticker}:${years}`,
    () => simulateRecovery(ticker, null, { n_draws: 20000 }, years), [ticker, years]);

  const bridge = ov.data?.economic_debt_bridge;
  const es = hz.data?.executive_summary;
  const flags = ov.data?.forensic_flags || [];
  // Phase 3 PD × LGD: both payloads already load on this page, so EL is a cross-multiply here.
  const pd12 = es?.distress_pd?.["12m"];
  const elPct = (t) => (pd12 == null ? null : 100 * pd12 * (1 - t["mean_recovery_%"] / 100));
  const elTotal = pd12 == null || !rec.data ? null
    : pd12 * (rec.data.tranches || []).reduce((s, t) => s + t.face * (1 - t["mean_recovery_%"] / 100), 0);
  const issuer = ov.data?.header?.issuer || hz.data?.issuer?.name || ticker;

  return (
    <div>
      <div className="mb-6 flex items-baseline gap-3">
        <h1 className="text-xl font-semibold text-slate-100">{issuer}</h1>
        <span className="font-mono text-sm text-slate-500">{ticker}</span>
        {ov.data?.header?.from_cache && (
          <span className="text-[10px] uppercase tracking-wide text-slate-600">snapshot cache</span>
        )}
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card title="How risky is this company?" to={`/company/${ticker}/risk`} toLabel="default risk"
          loading={hz.loading} error={hz.error}>
          <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
            <div>
              <div className="font-mono text-4xl" style={{ color: riskColor(es?.overall_risk) }}>
                {fmt(es?.overall_risk, 1)}
              </div>
              <div className="text-[10px] uppercase tracking-wide text-slate-500">composite risk / 100</div>
            </div>
            <div className="space-y-1 text-xs text-slate-400">
              <div>distance-to-default: <span className="font-mono text-slate-200">{fmt(es?.distance_to_default, 2)}σ</span></div>
              <div>12m PD (Merton): <span className="font-mono text-slate-200">{es?.distress_pd?.["12m"] != null ? `${fmt(100 * es.distress_pd["12m"], 1)}%` : "–"}</span></div>
              <div>trend: <span className={`font-semibold ${es?.trend?.direction === "worsening" ? "text-rose-300" : es?.trend?.direction === "improving" ? "text-emerald-300" : "text-slate-200"}`}>{es?.trend?.direction || "–"}</span></div>
            </div>
          </div>
        </Card>

        <Card title="How leveraged is it — and where is it hiding?" to={`/company/${ticker}/capital`} toLabel="capital structure"
          loading={ov.loading} error={ov.error}>
          <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
            <div>
              <div className="font-mono text-4xl text-slate-100">
                {bridge?.reported_leverage?.display || "–"}
                <span className="mx-2 text-xl text-slate-500">→</span>
                <span className="text-rose-300">{bridge?.economic_leverage?.display || "–"}</span>
              </div>
              <div className="text-[10px] uppercase tracking-wide text-slate-500">reported → economic leverage</div>
            </div>
            <div className="space-y-1 text-xs text-slate-400">
              <div>reported debt: <span className="font-mono text-slate-200">{bridge?.reported_debt?.display || "–"}</span></div>
              <div>economic debt: <span className="font-mono text-slate-200">{bridge?.economic_debt?.display || "–"}</span></div>
              <div>{(ov.data?.obs_items || []).length} off-balance-sheet findings</div>
            </div>
          </div>
        </Card>

        <Card title="What is the fulcrum, and what does it recover?" to={`/company/${ticker}/recovery`} toLabel="recovery"
          loading={rec.loading} error={rec.error}>
          {rec.data && (
            <>
              <div className="mb-2">
                <span className="font-mono text-lg text-rose-300">{rec.data.fulcrum || "no fulcrum (all whole)"}</span>
                <span className="ml-3 text-xs text-slate-500">EV median {fmt(rec.data.ev?.median, 0)} vs face {fmt(rec.data.total_face, 0)} $mm</span>
              </div>
              <div className="space-y-1 text-xs">
                {(rec.data.tranches || []).slice(0, 4).map((t) => (
                  <div key={t.tranche} className="flex items-center gap-2">
                    <span className={`w-44 truncate ${t.is_fulcrum ? "text-rose-300" : "text-slate-400"}`} title={t.tranche}>{t.tranche}</span>
                    <div className="h-1.5 flex-1 rounded bg-ink-700">
                      <div className="h-1.5 rounded bg-accent" style={{ width: `${Math.min(100, t["mean_recovery_%"])}%` }} />
                    </div>
                    <span className="w-12 text-right font-mono text-slate-200">{fmt(t["mean_recovery_%"], 0)}¢</span>
                    {pd12 != null && (
                      <span className="w-16 text-right font-mono text-slate-500" title="12m PD × (1 − mean recovery)">
                        EL {fmt(elPct(t), 2)}%
                      </span>
                    )}
                  </div>
                ))}
                {(rec.data.tranches || []).length > 4 && (
                  <div className="text-slate-600">+ {(rec.data.tranches || []).length - 4} more tranches</div>
                )}
              </div>
              {elTotal != null && (
                <div className="mt-2 text-xs text-slate-400">
                  12m expected loss: <span className="font-mono text-rose-300">{fmt(elTotal, 0)} $mm</span>
                  <span className="ml-2 text-slate-600">Merton 12m PD × (1 − mean recovery) × face</span>
                </div>
              )}
              <div className="mt-2 text-[10px] text-slate-600">default assumptions — tune on the Recovery tab</div>
            </>
          )}
        </Card>

        <Card title="What should I read first?" to={`/company/${ticker}/capital`} toLabel="forensics"
          loading={ov.loading} error={ov.error}>
          {flags.length === 0 && <div className="text-xs text-slate-500">No forensic divergence flags fired.</div>}
          <div className="space-y-2">
            {flags.slice(0, 4).map((f, i) => (
              <div key={i} className="text-xs">
                <span className={`mr-2 rounded px-1.5 py-0.5 text-[9px] uppercase ${f.severity === "high" ? "bg-rose-500/20 text-rose-300" : f.severity === "watch" ? "bg-amber-500/20 text-amber-300" : "bg-ink-700 text-slate-400"}`}>
                  {f.severity}
                </span>
                <span className="text-slate-300">{f.narrative}</span>
              </div>
            ))}
            {(ov.data?.warnings || []).length > 0 && (
              <div className="text-[11px] text-slate-600">{ov.data.warnings.length} pipeline warning(s) — see Capital Structure</div>
            )}
          </div>
        </Card>
      </div>

      <WhatChangedCard ov={ov.data} />
    </div>
  );
}

// Phase 4.6: latest vs prior period, biggest movers first. Quarterly TTM cadence when the
// issuer's 10-Q XBRL supports it (labels like "Q3 2025"), else annual FY vs FY.
function WhatChangedCard({ ov }) {
  const changes = ov?.what_changed || [];
  if (changes.length === 0) return null;
  const c0 = changes[0];
  const period = (label, fy) => label || `FY${fy}`;
  const lev = (ov.leverage_timeline || []).filter((p) => p.leverage != null).slice(-5);
  const val = (c, v) => (c.unit === "x" ? `${v.toFixed(1)}x` : `$${(v / 1e9).toFixed(1)}B`);
  return (
    <div className="mt-4 rounded-xl border border-ink-700 bg-ink-800/50 p-4">
      <div className="mb-3 flex items-baseline justify-between">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
          What changed — {period(c0.prior_label, c0.prior_fy)} → {period(c0.latest_label, c0.latest_fy)}
          {c0.latest_label && <span className="ml-2 normal-case text-slate-600">(flows are TTM)</span>}
        </div>
        {lev.length >= 2 && (
          <div className="font-mono text-[11px] text-slate-500">
            leverage {lev.map((p) => `${p.leverage.toFixed(1)}x`).join(" → ")}
            <span className="ml-1 text-slate-600">
              ({period(lev[0].label, lev[0].fiscal_year)}–{period(lev[lev.length - 1].label, lev[lev.length - 1].fiscal_year)})
            </span>
          </div>
        )}
      </div>
      <div className="flex flex-wrap gap-x-8 gap-y-2">
        {changes.slice(0, 5).map((c) => (
          <div key={c.metric} className="text-xs">
            <span className="text-slate-400">{c.metric}: </span>
            <span className="font-mono text-slate-200">{val(c, c.prior)} → {val(c, c.latest)}</span>
            <span className={`ml-1.5 font-mono font-semibold ${c.direction === "worse" ? "text-rose-300" : "text-emerald-300"}`}>
              {c.delta_pct > 0 ? "▲" : "▼"}{Math.abs(c.delta_pct)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
