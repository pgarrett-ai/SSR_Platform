import React, { useMemo, useState } from "react";
import { fetchOptions } from "../api.js";
import { useAsync } from "../cache.js";
import CitedNumber from "./CitedNumber.jsx";
import { Badge, Loading, Td, Th } from "../ui/index.jsx";

// Company options (Moyer ch. 11): with the clock running, what can the company still
// do — buy in debt below par, exchange it, or sell assets? The server payload is the
// deterministic feasibility read; the asset-sale explorer is client-side arithmetic
// (F7 IrrMatrix precedent). Offer terms live on the Recovery page's Exchange analyzer.

const VERDICT_TONE = {
  viable: "ok", nothing_to_capture: "neutral", no_window: "high", unknown: "neutral",
};
const WHO_TONE = { company: "ok", creditors: "high", unclear: "neutral" };
const n1 = (v) => (v == null ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: 1 }));

export default function OptionsCard({ ticker, years }) {
  const { data, loading, error } = useAsync(
    `options:${ticker}:${years}`, () => fetchOptions(ticker, years), [ticker, years]);
  const [soldE, setSoldE] = useState(0);          // EBITDA sold ($mm)
  const [mult, setMult] = useState(6);            // sale multiple (3–10x)
  const [atMarket, setAtMarket] = useState(true); // repurchase-at-market vs retire-at-face

  const sale = data?.asset_sale_inputs || {};
  const E = sale.ebitda_mm, F = sale.total_face_mm, M = sale.total_market_mm;
  const qmin = sale.quote_min;

  // pro-forma leverage = (F − retired) ÷ (E − sold); implied stub price = the market's
  // implied multiple (Σ market ÷ E) × remaining EBITDA ÷ stub face (formula footer)
  const calc = useMemo(() => {
    if (!(E > 0) || !(F > 0)) return null;
    const proceeds = soldE * mult;
    const retired = Math.min(atMarket && qmin > 0 ? proceeds / (qmin / 100) : proceeds, F);
    const remE = E - soldE;
    const stubFace = F - retired;
    const impliedMult = M > 0 ? M / E : null;
    return {
      proceeds,
      retired,
      lev: remE > 0 ? stubFace / remE : null,
      stubPrice: impliedMult != null && stubFace > 0 ? (100 * impliedMult * remE) / stubFace : null,
      dilutive: mult < F / E,
      faceLev: F / E,
    };
  }, [E, F, M, qmin, soldE, mult, atMarket]);

  if (loading) return <Loading />;
  if (error) return <div className="text-xs text-rose-300">{error}</div>;
  if (!data) return null;
  if (!data.available) return <div className="text-xs text-slate-500">{data.note}</div>;

  const clock = data.clock || {};
  const buyback = data.buyback || {};
  const gate = data.exchange_gate || {};

  return (
    <div>
      {/* clock strip */}
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        <Badge tone={WHO_TONE[clock.who_controls] || "neutral"}
          title={clock.who_controls_note}>
          clock: {clock.who_controls}
        </Badge>
        <span className="text-slate-400">
          {clock.days_to_next_event != null ? (
            <>
              <span className="font-mono text-slate-200">{clock.days_to_next_event}d</span>
              {" "}to {clock.next_event?.kind} — {clock.next_event?.instrument}
              {" "}({clock.next_event?.date})
            </>
          ) : (
            "no dated events on the 24-month calendar"
          )}
        </span>
        {clock.runway_months != null && (
          <span className="text-slate-500">
            runway <span className="font-mono text-slate-300">{clock.runway_months} mo</span>
          </span>
        )}
      </div>
      {clock.healthsouth_flag && (
        <div className="mb-3 rounded-md border border-rose-500/40 bg-rose-500/10 p-2 text-xs text-rose-200">
          {clock.note}
        </div>
      )}

      {/* the 4 feasibility axes */}
      <div className="mb-4 flex flex-wrap gap-2">
        {(data.axes || []).map((a) => (
          <Badge key={a.key} tone={a.tone} title={a.detail}>{a.label}</Badge>
        ))}
      </div>

      {/* buyback */}
      <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-500"
        title={buyback.derivation}>
        Open-market buyback (ch. 11)
      </div>
      {buyback.available === false ? (
        <div className="mb-4 text-xs text-slate-500">{buyback.note}</div>
      ) : (
        <>
          <div className="mb-2 text-sm text-slate-200">
            Deployable <CitedNumber cv={buyback.deployable} className="text-slate-100" />
            <span className="ml-2 text-xs text-slate-500">{buyback.rp_note}</span>
          </div>
          <div className="mb-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-ink-600">
                  <Th>Instrument</Th>
                  <Th right>Quote</Th>
                  <Th right>Face $mm</Th>
                  <Th right title="face × quote ÷ 100 — the cost of retiring the whole issue at market">MV $mm</Th>
                  <Th right title="min(face, deployable ÷ price per 100) — cheapest quote first">Retirable</Th>
                  <Th right>% of issue</Th>
                  <Th>Feasible</Th>
                </tr>
              </thead>
              <tbody>
                {(buyback.rows || []).map((r) => (
                  <tr key={r.instrument} className="border-b border-ink-800">
                    <Td className="max-w-[16rem] truncate text-slate-300" title={r.instrument}>
                      {r.instrument}
                    </Td>
                    <Td right mono className="text-slate-200">{r.price == null ? "—" : r.price}</Td>
                    <Td right mono className="text-slate-300">{n1(r.face_mm)}</Td>
                    <Td right mono className="text-slate-300">{n1(r.market_mm)}</Td>
                    <Td right mono>
                      <CitedNumber cv={r.retirable} className="text-slate-100" />
                    </Td>
                    <Td right mono className="text-slate-300">
                      {r.retirable_pct == null ? "—" : `${r.retirable_pct}%`}
                    </Td>
                    <Td>
                      {r.feasible == null ? (
                        <Badge tone="neutral" title="no drop-file quote — repurchase price unknown">unquoted</Badge>
                      ) : r.feasible ? (
                        <Badge tone="ok">feasible</Badge>
                      ) : (
                        <Badge tone="high" title="deployable liquidity floored at 0 — no repurchase capacity">no</Badge>
                      )}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* exchange gate */}
      <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-500">
        Exchange-offer gate (ch. 11)
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-2 text-xs">
        <Badge tone={VERDICT_TONE[gate.verdict] || "neutral"}>
          {(gate.verdict || "").replace(/_/g, " ")}
        </Badge>
        <Badge tone={gate.gate_60d?.pass === false ? "high" : gate.gate_60d?.pass ? "ok" : "neutral"}
          title={gate.gate_60d?.note}>
          60d window {gate.gate_60d?.pass === false ? "closed" : gate.gate_60d?.pass ? "open" : "unknown"}
        </Badge>
        {gate.discount_capture_per_100 != null && (
          <Badge tone={gate.discount_capture_per_100 > 2 ? "ok" : "neutral"}
            title={`min unsecured quote ${gate.min_unsecured_quote}`}>
            capture {gate.discount_capture_per_100}/100
          </Badge>
        )}
        <span className="text-slate-400" title={gate.holdout_note}>
          {(gate.reasons || []).join(" · ")}
        </span>
      </div>
      <div className="mb-4 text-xs text-slate-500">claim status: {gate.claim_status}</div>

      {/* asset-sale explorer — client-side arithmetic */}
      <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-500"
        title={sale.derivation}>
        Asset-sale explorer (ch. 11)
      </div>
      {sale.note ? (
        <div className="text-xs text-slate-500">{sale.note}</div>
      ) : calc ? (
        <>
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-slate-400">
            <label className="flex items-center gap-2">
              <span className="text-[10px] uppercase tracking-wide text-slate-500">EBITDA sold $mm</span>
              <input type="range" min={0} max={Math.round(E)} step={1} value={soldE}
                onChange={(e) => setSoldE(Number(e.target.value))} className="w-40 accent-accent" />
              <span className="font-mono text-slate-100">{soldE}</span>
            </label>
            <label className="flex items-center gap-2">
              <span className="text-[10px] uppercase tracking-wide text-slate-500">Sale multiple</span>
              <input type="range" min={3} max={10} step={0.5} value={mult}
                onChange={(e) => setMult(Number(e.target.value))} className="w-32 accent-accent" />
              <span className="font-mono text-slate-100">{mult}x</span>
            </label>
            <label className="flex items-center gap-1.5"
              title="retire at face (par tender) vs repurchase at the cheapest market quote">
              <input type="checkbox" checked={atMarket}
                onChange={(e) => setAtMarket(e.target.checked)} className="accent-accent" />
              <span>repurchase at market{qmin != null ? ` (@ ${qmin})` : ""}</span>
            </label>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-x-6 gap-y-1 text-xs">
            <span className="text-slate-400">
              proceeds <span className="font-mono text-slate-200">${n1(calc.proceeds)}M</span>
            </span>
            <span className="text-slate-400">
              retires <span className="font-mono text-slate-200">${n1(calc.retired)}M</span> face
            </span>
            <span className="text-slate-400">
              pro-forma leverage{" "}
              <span className="font-mono text-slate-100">
                {calc.lev == null ? "n.m." : `${calc.lev.toFixed(1)}x`}
              </span>
              <span className="text-slate-600"> (was {calc.faceLev.toFixed(1)}x at face)</span>
            </span>
            {calc.stubPrice != null && (
              <span className="text-slate-400">
                implied stub price <span className="font-mono text-slate-100">{calc.stubPrice.toFixed(1)}</span>
              </span>
            )}
            <Badge tone={calc.dilutive ? "high" : "ok"}
              title="dilutive iff the sale multiple is below the face-leverage multiple — selling below your leverage raises pro-forma leverage">
              {calc.dilutive ? "dilutive" : "accretive"}
            </Badge>
          </div>
          <div className="mt-2 text-[11px] text-slate-500">{sale.derivation}</div>
        </>
      ) : (
        <div className="text-xs text-slate-500">face or EBITDA unavailable — explorer n.m.</div>
      )}
    </div>
  );
}
