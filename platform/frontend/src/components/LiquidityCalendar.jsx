import React from "react";
import CitedNumber from "./CitedNumber.jsx";
import { Badge, Td, Th } from "../ui/index.jsx";

// Liquidity-event calendar (Moyer ch. 8): every coupon and maturity over the next 24
// months against total liquidity — for a cash-burner with an undersecured bank, every
// coupon is a potential filing trigger.

const FLAG_LABEL = {
  coupon_at_risk: "coupon at risk",
  maturity_unfundable: "unfundable",
};

export default function LiquidityCalendar({ events, note }) {
  if (!events?.length) {
    return (
      <div className="text-xs text-slate-500">
        No coupon or maturity events in the next 24 months{note ? ` — ${note}` : ""}.
      </div>
    );
  }
  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-ink-600">
              <Th>Month</Th><Th>Event</Th><Th>Instrument</Th>
              <Th right>Amount</Th>
              <Th right title="event amount as % of cash + tagged undrawn credit">% of liquidity</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {events.map((e, i) => (
              <tr key={i} className={`border-b border-ink-800 ${e.flags?.length ? "bg-rose-500/5" : ""}`}>
                <Td mono className="text-slate-300">{e.date}</Td>
                <Td className={e.kind === "maturity" ? "text-slate-100" : "text-slate-400"}>{e.kind}</Td>
                <Td className="max-w-[280px] truncate text-slate-300" title={e.instrument}>{e.instrument}</Td>
                <Td right mono className="text-slate-200">
                  <CitedNumber cv={e.amount} />
                </Td>
                <Td right mono className="text-slate-400">
                  {e.pct_of_liquidity != null ? (
                    <span className="inline-flex items-center gap-2">
                      <span className="inline-block h-1.5 w-16 rounded bg-ink-700">
                        <span className="block h-1.5 rounded bg-accent"
                          style={{ width: `${Math.min(100, e.pct_of_liquidity)}%` }} />
                      </span>
                      {e.pct_of_liquidity}%
                    </span>
                  ) : "—"}
                </Td>
                <Td>
                  {(e.flags || []).map((f) => (
                    <Badge key={f} tone="high" className="mr-1">{FLAG_LABEL[f] || f}</Badge>
                  ))}
                  {e.assumption && (
                    <span className="text-[10px] text-slate-600" title={e.assumption}>ⓘ</span>
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {note && <div className="mt-2 text-[11px] text-slate-500">⚠ {note}</div>}
      <div className="mt-1 text-[11px] text-slate-600">
        coupon months anchored on the maturity anniversary (dates are not tagged in XBRL);
        contractual lease/pension payments not included
      </div>
    </div>
  );
}
