import React from "react";
import CitedNumber from "./CitedNumber.jsx";
import { Badge, Td, Th } from "../ui/index.jsx";

// Every coupon and maturity ahead against total liquidity — for a cash-burner with an
// undersecured bank, every coupon is a potential filing trigger. Events dated before the
// current month are dropped: the cache can be older than today.

const FLAG_LABEL = {
  coupon_at_risk: "coupon at risk",
  maturity_unfundable: "unfundable",
  stayed: "stayed (Ch. 11)",
};
// The automatic stay suspends pre-petition payments: a fact of the case, not an alarm.
const isAlarm = (f) => f !== "stayed";
const MAX_ROWS = 12;

export default function LiquidityCalendar({ events, note }) {
  const month = new Date().toISOString().slice(0, 7);
  const ahead = (events || []).filter((e) => (e.date || "").slice(0, 7) >= month);
  if (!ahead.length)
    return <p className="text-sm text-slate-400">No coupon or maturity dates ahead.</p>;
  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-ink-600">
              <Th>Month</Th><Th>Event</Th><Th>Instrument</Th>
              <Th right>Amount</Th>
              <Th right title="event amount as % of total liquidity: cash, short-term investments and tagged undrawn credit">% of liquidity</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {ahead.slice(0, MAX_ROWS).map((e, i) => (
              <tr key={i} className={`border-b border-ink-700/60 ${e.flags?.some(isAlarm) ? "bg-rose-500/5" : ""}`}>
                <Td mono className="text-slate-300">{e.date}</Td>
                <Td className={e.kind === "maturity" ? "text-slate-100" : "text-slate-400"}
                  title={e.assumption || undefined}>{e.kind}</Td>
                <Td className="max-w-[280px] truncate text-slate-300" title={e.instrument}>{e.instrument}</Td>
                <Td right mono className="text-slate-200">
                  <CitedNumber cv={e.amount} />
                </Td>
                <Td right mono className="text-slate-400">
                  {e.pct_of_liquidity != null ? `${e.pct_of_liquidity}%` : "—"}
                </Td>
                <Td>
                  {(e.flags || []).map((f) => (
                    <Badge key={f} tone={isAlarm(f) ? "high" : "neutral"} className="mr-1">{FLAG_LABEL[f] || f}</Badge>
                  ))}
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[11px] text-slate-400">
        {ahead.length > MAX_ROWS ? `Next ${MAX_ROWS} of ${ahead.length} dates. ` : ""}
        Coupon months are anchored on the maturity anniversary; lease and pension payments are
        not included.{note ? ` ${note}.` : ""}
      </p>
    </div>
  );
}
