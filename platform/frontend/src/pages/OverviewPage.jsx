import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { fetchHazard, fetchOverview, fetchRates, searchText, simulateRecovery } from "../api.js";
import { useAsync } from "../cache.js";
import { Badge, Button, Card, Input, Loading, Section, fmt, riskColor } from "../ui/index.jsx";

// Key reference rates strip — DB-stored observations with their as-of dates.
function KeyRates() {
  const { data } = useAsync("rates", () => fetchRates(), []);
  if (!data?.rates?.length) return null;
  return (
    <div className="mb-6 flex flex-wrap items-baseline gap-x-6 gap-y-2 rounded-xl border border-ink-700 bg-ink-900/60 px-4 py-3">
      <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Key rates</span>
      {data.rates.map((r) => (
        <span key={r.series} className="text-xs text-slate-400" title={`as of ${r.date}`}>
          {r.label}{" "}
          <span className="font-mono text-slate-200">{r.value.toFixed(2)}%</span>
        </span>
      ))}
      <span className="ml-auto text-[10px] text-slate-600" title={data.note}>
        LIBOR discontinued 6/2023
      </span>
    </div>
  );
}

// Snippets come from filing text via FTS5 snippet(); the only HTML we allow through
// is our own <mark> markers — everything else is escaped before rendering.
function markOnly(snippet) {
  const esc = snippet
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
  return esc
    .replaceAll("&lt;mark&gt;", "<mark class=\"bg-accent/30 text-white rounded px-0.5\">")
    .replaceAll("&lt;/mark&gt;", "</mark>");
}

// Badge uppercases its label, so raw source kinds need display names ("mdna" → MD&A).
const KIND_LABELS = { mdna: "MD&A" };

// Full-text search over this issuer's analyzed filings; hits open the Capital page,
// where the covenant packages and MD&A reader live.
function DocSearch({ ticker }) {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState(null);   // null = no search yet

  async function run(e) {
    e.preventDefault();
    if (!q.trim()) { setHits(null); return; }
    try {
      // The corpus stores the same clause across amendments/periods — dedupe for display.
      const seen = new Set();
      setHits((await searchText(q.trim(), ticker)).hits.filter((h) => {
        const k = `${h.source_kind}|${h.snippet}`;
        if (seen.has(k)) return false;
        seen.add(k);
        return true;
      }));
    } catch {
      setHits([]);
    }
  }

  return (
    <Section title="Document search" subtitle="covenants · OBS findings · MD&A for this issuer"
      className="mt-6">
      <form onSubmit={run} className="flex gap-2">
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="e.g. springing lien, restricted payments, going concern"
          className="w-full"
        />
        <Button type="submit">Search</Button>
      </form>
      {hits !== null && (
        <div className="mt-3">
          <div className="mb-2 text-[10px] uppercase tracking-wide text-slate-600">
            {hits.length} hit{hits.length === 1 ? "" : "s"}
          </div>
          {hits.length === 0 && (
            <p className="text-sm text-slate-500">No matches in this issuer's analyzed filings.</p>
          )}
          {hits.map((h, i) => (
            <button
              key={i}
              onClick={() => navigate(`/company/${ticker}/capital`)}
              className="mb-1 block w-full rounded-md border border-ink-700 px-3 py-2 text-left text-sm hover:border-accent"
              title="open on the Capital Structure page"
            >
              <Badge>{KIND_LABELS[h.source_kind] || h.source_kind}</Badge>
              <span
                className="ml-2 text-slate-400"
                dangerouslySetInnerHTML={{ __html: markOnly(h.snippet || "") }}
              />
            </button>
          ))}
        </div>
      )}
    </Section>
  );
}

// Company landing page: risk / leverage / recovery / flags, one card each, loading
// independently. "What changed" compares the two most recent periods.

function OverviewCard({ title, to, children, loading, error }) {
  return (
    <Card className="flex flex-col">
      <div className="mb-3 flex items-baseline justify-between">
        <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">{title}</div>
        {to && (
          <Link to={to} className="text-[11px] text-accent hover:underline">
            open →
          </Link>
        )}
      </div>
      {loading && <Loading />}
      {error && <div className="py-2 text-xs text-rose-300">{error}</div>}
      {!loading && !error && children}
    </Card>
  );
}

export default function OverviewPage({ ticker, years }) {
  const ov = useAsync(`overview:${ticker}:${years}`, () => fetchOverview(ticker, years), [ticker, years]);
  const hz = useAsync(`hazard:${ticker}`, () => fetchHazard(ticker, 10), [ticker]);
  const rec = useAsync(`recovery-quick:${ticker}:${years}`,
    () => simulateRecovery(ticker, null, { n_draws: 20000 }, years), [ticker, years]);

  const bridge = ov.data?.economic_debt_bridge;
  const es = hz.data?.executive_summary;
  const flags = ov.data?.forensic_flags || [];
  // PD × LGD: both payloads already load on this page, so EL is a cross-multiply here.
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
        {ov.data?.header?.from_cache && <Badge>cached</Badge>}
      </div>

      <KeyRates />

      <div className="grid gap-4 md:grid-cols-2">
        <OverviewCard title="Default risk" to={`/company/${ticker}/risk`}
          loading={hz.loading} error={hz.error}>
          <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
            <div>
              <div className="font-mono text-4xl" style={{ color: riskColor(es?.overall_risk) }}>
                {fmt(es?.overall_risk, 1)}
              </div>
              <div className="text-[10px] uppercase tracking-wide text-slate-500">composite risk / 100</div>
            </div>
            <div className="space-y-1 text-xs text-slate-400">
              <div>distance-to-default: <span className="font-mono text-slate-200">{es?.distance_to_default != null ? `${fmt(es.distance_to_default, 2)}σ` : "—"}</span></div>
              <div>12m PD (Merton): <span className="font-mono text-slate-200">{es?.distress_pd?.["12m"] != null ? `${fmt(100 * es.distress_pd["12m"], 1)}%` : "—"}</span></div>
              <div>trend: <span className={`font-semibold ${es?.trend?.direction === "worsening" ? "text-rose-300" : es?.trend?.direction === "improving" ? "text-emerald-300" : "text-slate-200"}`}>{es?.trend?.direction || "—"}</span></div>
            </div>
          </div>
        </OverviewCard>

        <OverviewCard title="Capital structure" to={`/company/${ticker}/capital`}
          loading={ov.loading} error={ov.error}>
          <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
            <div>
              <div className="font-mono text-4xl text-slate-100">
                {bridge?.reported_leverage?.display || "—"}
                <span className="mx-2 text-xl text-slate-500">→</span>
                <span className="text-rose-300">{bridge?.economic_leverage?.display || "—"}</span>
              </div>
              <div className="text-[10px] uppercase tracking-wide text-slate-500">reported → economic leverage</div>
            </div>
            <div className="space-y-1 text-xs text-slate-400">
              <div>reported debt: <span className="font-mono text-slate-200">{bridge?.reported_debt?.display || "—"}</span></div>
              <div>economic debt: <span className="font-mono text-slate-200">{bridge?.economic_debt?.display || "—"}</span></div>
              <div>{(ov.data?.obs_items || []).length} off-balance-sheet findings</div>
            </div>
          </div>
        </OverviewCard>

        <OverviewCard title="Recovery" to={`/company/${ticker}/recovery`}
          loading={rec.loading} error={rec.error}>
          {rec.data && (
            <>
              <div className="mb-2">
                <span className="font-mono text-lg text-rose-300">{rec.data.fulcrum || "no fulcrum — all classes covered"}</span>
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
              <div className="mt-2 text-[10px] text-slate-600">default assumptions — tune on Recovery</div>
            </>
          )}
        </OverviewCard>

        <OverviewCard title="Forensic flags" to={`/company/${ticker}/capital`}
          loading={ov.loading} error={ov.error}>
          {flags.length === 0 && <div className="text-xs text-slate-500">No forensic divergence flags fired.</div>}
          <div className="space-y-2">
            {flags.slice(0, 4).map((f, i) => (
              <div key={i} className="text-xs">
                <Badge tone={f.severity === "high" ? "high" : f.severity === "watch" ? "watch" : "neutral"} className="mr-2">
                  {f.severity}
                </Badge>
                <span className="text-slate-300">{f.narrative}</span>
              </div>
            ))}
            {(ov.data?.warnings || []).length > 0 && (
              <div className="text-[11px] text-slate-600">{ov.data.warnings.length} pipeline warning{ov.data.warnings.length === 1 ? "" : "s"} — see Capital Structure</div>
            )}
          </div>
        </OverviewCard>
      </div>

      <WhatChangedCard ov={ov.data} />

      <DocSearch ticker={ticker} />
    </div>
  );
}

// Latest vs prior period, biggest movers first. Quarterly TTM cadence when the
// issuer's 10-Q XBRL supports it (labels like "Q3 2025"), else annual FY vs FY.
function WhatChangedCard({ ov }) {
  const changes = ov?.what_changed || [];
  if (changes.length === 0) return null;
  const c0 = changes[0];
  const period = (label, fy) => label || `FY${fy}`;
  const lev = (ov.leverage_timeline || []).filter((p) => p.leverage != null).slice(-5);
  const val = (c, v) => (c.unit === "x" ? `${v.toFixed(1)}x` : `$${(v / 1e9).toFixed(1)}B`);
  return (
    <Card className="mt-4">
      <div className="mb-3 flex items-baseline justify-between">
        <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
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
    </Card>
  );
}
