import React from "react";
import { Link } from "react-router-dom";
import CitedNumber from "./CitedNumber.jsx";
import { fmt, fmtNum, fmtPct } from "../ui/index.jsx";

// The whole credit story on every company tab: identity, then one cell per question
// (what they owe, the liquidity behind it, default odds, recovery). Each cell renders
// from its own payload, so a slow hazard run never holds back the debt read.

const PENDING = <span className="text-slate-500">…</span>;
const DOT = <span className="text-slate-600">·</span>;

// Stretched link: empty space in the cell navigates (cmd-click included), while cited
// numbers paint above the link's overlay and keep their hover card.
function Cell({ to, label, active, children }) {
  return (
    <div
      className={`relative min-w-0 rounded-lg border px-3 py-2 transition-colors ${
        active ? "border-accent/70 bg-accent/5" : "border-ink-700 bg-ink-800/40 hover:border-ink-600"
      }`}
    >
      <Link
        to={to}
        className="text-[11px] font-semibold uppercase tracking-wider text-slate-500 after:absolute after:inset-0"
      >
        {label}
      </Link>
      <div className="mt-0.5 text-sm text-slate-200">{children}</div>
    </div>
  );
}

const pd = (x) => (x < 0.001 ? "<0.1%" : fmtPct(x, 1));
const runwayTone = (m) => (m < 6 ? "text-rose-300" : m < 12 ? "text-amber-300" : "text-slate-400");

export default function IssuerStrip({ ticker, tab, overview, hazard, recovery }) {
  const ov = overview.data;
  const name = ov?.header?.issuer || hazard.data?.issuer?.name;
  const resolved = ov?.header?.resolved_ticker?.toUpperCase();
  const ch11 = ov?.chapter11;
  const b = ov?.economic_debt_bridge;
  const liq = ov?.liquidity;
  const es = hazard.data?.executive_summary;
  const base = `/company/${ticker}`;
  const fallback = (s) => (s.loading ? PENDING : <span className="text-slate-500" title={s.error || undefined}>—</span>);

  // Leverage over non-positive EBITDA is not meaningful; economic debt still reads.
  const nm = b?.ebitda?.value != null && b.ebitda.value <= 0;
  const fulcrum = recovery &&
    (recovery.tranches || []).find((t) => t.is_fulcrum || t.tranche === recovery.fulcrum);

  const identity = [
    name && <span className="font-semibold text-slate-100">{name}</span>,
    <span className="font-mono text-slate-300">
      {ticker}
      {resolved && resolved !== ticker && (
        <span className={resolved.endsWith("Q") ? "text-amber-300" : ""}> → {resolved}</span>
      )}
    </span>,
    liq?.as_of_label && <span>data as of {liq.as_of_label}</span>,
    ch11?.petition_date && (
      <span className="text-amber-300">
        In Chapter 11 since{" "}
        {ch11.citation
          ? <CitedNumber cv={{ display: ch11.petition_date, citation: ch11.citation }} />
          : ch11.petition_date}
      </span>
    ),
  ].filter(Boolean);

  return (
    <div className="mb-6">
      <div className="mb-2 flex flex-wrap items-baseline gap-x-2 text-sm text-slate-400">
        {identity.map((part, i) => (
          <React.Fragment key={i}>{i > 0 && DOT}{part}</React.Fragment>
        ))}
      </div>

      <div className={`grid grid-cols-2 gap-2 ${recovery ? "md:grid-cols-4" : "md:grid-cols-3"}`}>
        <Cell to={`${base}/capital`} label="Owe" active={tab === "capital"}>
          {!b ? fallback(overview) : (
            <>
              <div className="truncate">
                <CitedNumber cv={b.reported_debt} /> <span className="text-slate-500">→</span>{" "}
                <CitedNumber cv={b.economic_debt} className="text-slate-100" />
              </div>
              <div className="truncate text-xs text-slate-400">
                {nm ? (
                  <><CitedNumber cv={{ ...b.economic_leverage, display: "n.m." }} /> leverage · EBITDA ≤ 0</>
                ) : (
                  <>
                    <CitedNumber cv={b.reported_leverage} /> → <CitedNumber cv={b.economic_leverage} />
                    <span className="text-slate-500"> reported → economic</span>
                  </>
                )}
              </div>
            </>
          )}
        </Cell>

        <Cell to={`${base}/capital`} label="Liquidity" active={tab === "capital"}>
          {!liq ? fallback(overview) : (
            <>
              <CitedNumber cv={liq.total_liquidity} className="text-slate-100" />
              <div className="truncate text-xs text-slate-400">
                cash + undrawn
                {liq.is_distressed && liq.runway_months != null && (
                  <span className={runwayTone(liq.runway_months)}>
                    {" "}· runway {liq.runway_months.toFixed(0)} mo
                  </span>
                )}
              </div>
            </>
          )}
        </Cell>

        <Cell to={`${base}/risk`} label="Default" active={tab === "risk"}>
          {es?.distress_pd?.["12m"] == null ? fallback(hazard) : (
            <>
              <div className="truncate font-mono text-slate-100">
                {pd(es.distress_pd["12m"])}
                {es.distance_to_default != null && (
                  <span className="text-slate-400"> · DD {fmtNum(es.distance_to_default, 1)}σ</span>
                )}
              </div>
              <div className="truncate text-xs text-slate-400">12m PD (Merton, risk-neutral)</div>
            </>
          )}
        </Cell>

        {recovery && (
          <Cell to={`${base}/recovery`} label="Recover" active={tab === "recovery"}>
            <div
              title={[recovery.saved_at && `run ${recovery.saved_at.slice(0, 10)}`,
                recovery.mode === "liquidation" ? "liquidation" : "Monte Carlo"].filter(Boolean).join(" · ")}
            >
              <div className="truncate text-slate-100">
                {recovery.fulcrum ? `Fulcrum: ${recovery.fulcrum}` : "No fulcrum — every class covered"}
              </div>
              {fulcrum?.mean_recovery_pct != null && (
                <div className="text-xs text-slate-400">
                  mean <span className="font-mono text-slate-200">{fmt(fulcrum.mean_recovery_pct, 0)}%</span> of claim
                </div>
              )}
            </div>
          </Cell>
        )}
      </div>
    </div>
  );
}
