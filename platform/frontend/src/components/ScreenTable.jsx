import React, { useEffect, useState } from "react";
import { fetchScreen } from "../api.js";
import { Td, Th, fmtLev, rowClass } from "../ui/index.jsx";

export default function ScreenTable({ onPick }) {
  const [rows, setRows] = useState([]);

  useEffect(() => {
    fetchScreen().then(setRows).catch(() => {});
  }, []);

  return (
    <div className="mt-12 text-left">
      {rows.length > 0 && (
        <>
          <div className="mb-2 text-[10px] uppercase tracking-wide text-slate-600">Analyzed companies</div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-ink-600">
                  <Th>Ticker</Th>
                  <Th>Issuer</Th>
                  <Th right>Reported lev</Th>
                  <Th right>Economic lev</Th>
                  <Th right className="cursor-help" title="(Σ debt at market − cash) ÷ EBITDA — TRACE drop-file quotes; computed at snapshot time, so it lags a quotes refresh until the next run">Net@mkt lev</Th>
                  <Th right className="cursor-help" title="creation multiple through the fulcrum class at market (Moyer) — computed at snapshot time">Creation x</Th>
                  <Th right>Flags</Th>
                  <Th right className="cursor-help" title="composite risk 0-100 · trained PD implied rating — fills in after a Default Risk run">Risk</Th>
                  <Th right className="cursor-help" title="Moyer distressed fact pattern: stock < $1 and an unsecured quote < 60">⚑</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.ticker}
                    onClick={() => onPick(r.ticker)}
                    className={`cursor-pointer ${rowClass}`}
                  >
                    <Td mono className="text-slate-200">{r.ticker}</Td>
                    <Td className="text-slate-400">{r.issuer || "—"}</Td>
                    <Td right mono className="text-slate-300">{fmtLev(r.reported_leverage)}</Td>
                    <Td right mono className="text-slate-300">{fmtLev(r.economic_leverage)}</Td>
                    <Td right mono className="text-slate-300">{fmtLev(r.net_market_leverage)}</Td>
                    <Td right mono className="text-slate-300">{fmtLev(r.creation_multiple_fulcrum)}</Td>
                    <Td right mono className="text-slate-400">{r.flag_count ?? "—"}</Td>
                    <Td right mono className="text-slate-300">
                      {r.overall_risk == null ? "—" : `${r.overall_risk.toFixed(1)}${r.implied_rating ? ` · ${r.implied_rating}` : ""}`}
                    </Td>
                    <Td right className="text-rose-300">{r.distress_badge ? "⚑" : ""}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-2 text-[11px] text-slate-500">
            one row per issuer · latest snapshot
          </div>
        </>
      )}
    </div>
  );
}
