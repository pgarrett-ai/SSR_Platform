import React from "react";
import CitedNumber from "./CitedNumber.jsx";
import { Badge, Td, Th, rowClass } from "../ui/index.jsx";

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
              <Td mono className="text-[12px] text-slate-300">{d.coupon || "—"}</Td>
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
