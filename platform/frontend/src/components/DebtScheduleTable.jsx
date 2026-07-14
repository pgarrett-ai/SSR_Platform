import React from "react";
import CitedNumber from "./CitedNumber.jsx";
import { Badge, Td, Th, rowClass } from "../ui/index.jsx";

// Filing text is formulaic: "variable interest rate of 6.00%" → "6.00% var.",
// "fixed interest rates ranging from 2.88% to 7.15%, averaging 3.95%" → "2.88–7.15% avg 3.95%".
// ponytail: regex heuristic — unparseable strings return null and the cell falls back to
// the full text; the exact filing wording is always in the title tooltip.
function compactCoupon(s) {
  if (!s || s.length <= 14) return s;
  const pcts = [...s.matchAll(/(\d+(?:\.\d+)?)\s*%/g)].map((m) => m[1]);
  if (!pcts.length) return null;
  const varMark = /variable|floating/i.test(s) ? " var." : "";
  if (pcts.length >= 3 && /rang/i.test(s)) return `${pcts[0]}–${pcts[1]}% avg ${pcts[2]}%`;
  if (pcts.length === 2 && /rang|\bto\b/i.test(s)) return `${pcts[0]}–${pcts[1]}%`;
  return `${pcts[0]}%${varMark}`;
}

export default function DebtScheduleTable({ instruments }) {
  if (!instruments || instruments.length === 0) {
    return (
      <p className="text-sm text-slate-400">
        No instrument-level detail available for this issuer.
      </p>
    );
  }
  const hasObligor = instruments.some((d) => d.obligor);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-ink-600">
            <Th>Instrument</Th>
            <Th right>Outstanding</Th>
            <Th>Coupon</Th>
            <Th>Maturity</Th>
            <Th>Lien / seniority</Th>
            {hasObligor && <Th>Obligor</Th>}
          </tr>
        </thead>
        <tbody>
          {instruments.map((d, i) => (
            <tr key={i} className={rowClass}>
              <Td className="text-slate-200">{d.instrument}</Td>
              <Td right>
                <CitedNumber cv={d.outstanding || d.principal} />
              </Td>
              <Td mono className="text-[12px] text-slate-300">
                <span title={d.coupon || undefined}>{compactCoupon(d.coupon) || d.coupon || "—"}</span>
              </Td>
              <Td mono className="text-[12px] text-slate-300">{d.maturity || "—"}</Td>
              <Td className="text-[12px]">
                {d.secured != null && (
                  <Badge tone={d.secured ? "ok" : "neutral"} className="mr-2">
                    {d.secured ? "secured" : "unsecured"}
                  </Badge>
                )}
                <span className="text-slate-400">{d.seniority || ""}</span>
              </Td>
              {hasObligor && (
                <Td className="text-[12px] text-slate-400">{d.obligor || "—"}</Td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
