import React from "react";
import CitedNumber from "./CitedNumber.jsx";

export default function DebtScheduleTable({ instruments }) {
  if (!instruments || instruments.length === 0) {
    return (
      <p className="text-sm text-slate-400">
        Instrument-level detail (coupon, maturity, lien) is extracted from the debt footnote when an
        Anthropic API key is set.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-ink-600 text-slate-400">
            <th className="py-2 pr-3 text-left font-medium">Instrument</th>
            <th className="py-2 px-3 text-right font-medium">Outstanding</th>
            <th className="py-2 px-3 text-left font-medium">Coupon</th>
            <th className="py-2 px-3 text-left font-medium">Maturity</th>
            <th className="py-2 px-3 text-left font-medium">Lien / seniority</th>
          </tr>
        </thead>
        <tbody>
          {instruments.map((d, i) => (
            <tr key={i} className="border-b border-ink-700/60 hover:bg-ink-700/30">
              <td className="py-2 pr-3 text-slate-200">{d.instrument}</td>
              <td className="py-2 px-3 text-right">
                <CitedNumber cv={d.outstanding || d.principal} />
              </td>
              <td className="py-2 px-3 font-mono text-[12px] text-slate-300">{d.coupon || "—"}</td>
              <td className="py-2 px-3 font-mono text-[12px] text-slate-300">{d.maturity || "—"}</td>
              <td className="py-2 px-3 text-[12px]">
                {d.secured != null && (
                  <span
                    className={`mr-2 rounded px-1.5 py-0.5 text-[10px] uppercase ${
                      d.secured ? "bg-emerald-500/15 text-emerald-300" : "bg-slate-500/15 text-slate-300"
                    }`}
                  >
                    {d.secured ? "secured" : "unsecured"}
                  </span>
                )}
                <span className="text-slate-400">{d.seniority || ""}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-[11px] text-slate-500">
        Extracted from the long-term-debt footnote — hover any amount for the verbatim source text.
      </p>
    </div>
  );
}
