import React, { useMemo, useState } from "react";
import { fetchLadder } from "../api.js";
import { useAsync } from "../cache.js";
import CitedNumber from "./CitedNumber.jsx";
import { Loading, Td, Th } from "../ui/index.jsx";

// Effective cost basis (Moyer ch. 5): quote + accrued unless the paper trades flat;
// claim/100 at accreted value; cash-at-risk = basis − coupons received before the
// restructuring date. Rides the ladder payload — same cache key as CreationLadder,
// one fetch for the whole Moyer band. Flat toggle + coupon counting are client-side.

const n2 = (v) => (v == null ? "—" : v.toFixed(2));

export default function TradeBasis({ ticker, years }) {
  const { data, loading, error } = useAsync(
    `ladder:${ticker}:${years}`, () => fetchLadder(ticker, years), [ticker, years]);
  const [flat, setFlat] = useState({});          // instrument -> trading-flat toggle
  const [restructure, setRestructure] = useState("");

  const rows = data?.basis?.rows || [];
  // default restructure date: earliest maturity on the 24-mo event calendar
  const restructureDate = restructure
    || (data?.basis?.default_restructure ? `${data.basis.default_restructure}-01`.slice(0, 10) : "");

  const computed = useMemo(() => rows.map((r) => {
    const isFlat = !!flat[r.instrument];   // hint is the amber dot, not a default
    const basis = isFlat ? r.quote : +(r.quote + (r.accrued?.value || 0)).toFixed(2);
    const claim = r.claim_per_100?.value;
    const coupons = (r.coupons || []).filter((c) => restructureDate && c.date < restructureDate);
    const received = coupons.reduce((a, c) => a + c.amount, 0);
    return {
      ...r, isFlat, effBasis: basis,
      costPct: claim > 0 ? +(100 * basis / claim).toFixed(1) : null,
      couponsReceived: +received.toFixed(3),
      cashAtRisk: +(basis - received).toFixed(2),
    };
  }), [rows, flat, restructureDate]);

  if (loading) return <Loading />;
  if (error) return <div className="text-xs text-rose-300">{error}</div>;
  if (!rows.length)
    return <div className="text-xs text-slate-500">
      {data?.basis?.note || "No matched quotes — TRACE drop-file empty or unmatched."}
    </div>;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-slate-400">
        <label className="flex items-center gap-1.5">
          <span className="text-[10px] uppercase tracking-wide text-slate-500"
            title="coupons before this date count as cash returned; default = the nearest maturity on the event calendar">
            Restructuring date:
          </span>
          <input type="date" value={restructureDate}
            onChange={(e) => setRestructure(e.target.value)}
            className="rounded-md border border-ink-600 bg-ink-800 px-2 py-1 font-mono text-xs text-slate-100 outline-none focus:border-accent" />
        </label>
        <span className="text-slate-500">{data.basis.hint}</span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-ink-600">
              <Th>Instrument</Th>
              <Th right>Quote</Th>
              <Th right title="coupon × days/360 since the last coupon (30/360)">Accrued/100</Th>
              <Th title="trading flat: the quote already includes (worthless) accrued — pay the quote only">Flat</Th>
              <Th right title="quote + accrued, or quote alone when trading flat — the real entry price">Effective basis</Th>
              <Th right title="accreted value + accrued — unamortized OID disallowed (§502(b)(2))">Claim/100</Th>
              <Th right title="effective basis ÷ claim — cents on the dollar of claim actually paid">Cost % of claim</Th>
              <Th right title="quote × face ÷ accreted — the true discount where OID (Moyer ch. 5)">% of accreted</Th>
              <Th right title="effective basis − coupons received before the restructuring date">Cash-at-risk</Th>
            </tr>
          </thead>
          <tbody>
            {computed.map((r) => (
              <tr key={r.instrument} className="border-b border-ink-800">
                <Td className="text-slate-300">
                  {r.instrument}
                  {r.oid && <span className="ml-1.5 text-[9px] uppercase text-amber-400/80"
                    title="original-issue discount — claim accretes toward face">oid</span>}
                </Td>
                <Td right mono className="text-slate-200">{n2(r.quote)}</Td>
                <Td right mono className="text-slate-300">
                  <CitedNumber cv={r.accrued} className="text-slate-300" />
                </Td>
                <Td className="text-center">
                  <span className="inline-flex items-center gap-1">
                    <input type="checkbox" checked={r.isFlat}
                      onChange={(e) => setFlat((f) => ({ ...f, [r.instrument]: e.target.checked }))} />
                    {r.flat_hint && (
                      <span className="h-1.5 w-1.5 rounded-full bg-amber-400"
                        title="likely trades flat — next coupon at risk or zero-coupon" />
                    )}
                  </span>
                </Td>
                <Td right mono className="text-slate-100">{n2(r.effBasis)}</Td>
                <Td right mono className="text-slate-300">
                  <CitedNumber cv={r.claim_per_100} className="text-slate-300" />
                </Td>
                <Td right mono className="text-slate-100">
                  {r.costPct == null ? "—" : `${r.costPct}%`}
                </Td>
                <Td right mono className={r.oid ? "text-slate-100" : "text-slate-500"}>
                  {r.pct_of_accreted == null ? "—" : n2(r.pct_of_accreted)}
                </Td>
                <Td right mono className="text-slate-200"
                  title={`${r.couponsReceived} of coupons received before ${restructureDate || "—"}; worst case (no coupons paid) = ${n2(r.effBasis)}`}>
                  {n2(r.cashAtRisk)}
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-2 text-[11px] text-slate-500">{data.basis.derivation}</div>
    </div>
  );
}
