import React from "react";
import { fetchRefiWall } from "../api.js";
import { useAsync } from "../cache.js";
import CitedNumber from "./CitedNumber.jsx";
import { Badge, EmptyState, Skeleton, Td, Th } from "../ui/index.jsx";

// Refi-wall sequencing: can each maturity bucket be repaid internally, and if not, is
// the market open to refinance it (bond marks: YTM ≥ 40% = markets closed)?

const VERDICT_TONE = { unrefinanceable: "high", refi_needed: "watch", fundable: "ok" };

export default function RefiWall({ ticker, years }) {
  const { data, loading, error } = useAsync(
    `refi:${ticker}:${years}`, () => fetchRefiWall(ticker, years), [ticker, years]);

  if (loading) return <Skeleton rows={3} />;
  if (error) return <div className="mt-3 text-xs text-rose-300">{error}</div>;
  if (!data) return <EmptyState>No refi-wall read — needs a cached overview with maturities.</EmptyState>;
  if (!data.available) return <div className="mt-3 text-xs text-slate-500">{data.note}</div>;

  return (
    <div className="mt-5">
      <div className="mb-1 text-xs text-slate-500">
        Refi-wall sequencing — repay internally, or refinance into this market?
      </div>
      {(data.notes || []).map((n, i) => (
        <div key={i} className="mb-1 text-[11px] text-amber-300/80">⚠ {n}</div>
      ))}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-ink-600">
              <Th>Year</Th>
              <Th>Instruments</Th>
              <Th right title="Σ face due in the bucket ($mm)">Face</Th>
              <Th right title="min(face, liquidity + cumulative sweep capacity at flat 2% growth − earlier wall faces) — sequential funding, front to back">Repayable internally</Th>
              <Th right title="face − repayable internally, floored at 0">Refi need</Th>
              <Th right title="bucket quote's YTM — ≥ 40% means the refi market is closed; refi prob = (bucket price − longer pari price) ÷ (100 − longer pari price)">YTM / refi prob</Th>
              <Th>Verdict</Th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r) => (
              <tr key={r.year} className="border-b border-ink-800 align-top">
                <Td mono className="text-slate-200">{r.year}</Td>
                <Td className="max-w-[15rem] truncate text-xs text-slate-400"
                  title={(r.instruments || []).join(", ")}>
                  {(r.instruments || []).join(", ")}
                </Td>
                <Td right mono><CitedNumber cv={r.face} className="text-slate-200" /></Td>
                <Td right mono><CitedNumber cv={r.repayable} className="text-slate-300" /></Td>
                <Td right mono>
                  <CitedNumber cv={r.refi_need}
                    className={r.refi_need?.value > 0 ? "text-amber-300" : "text-slate-400"} />
                </Td>
                <Td right mono className="text-slate-300" title={r.refi_prob_note || undefined}>
                  {r.quote?.ytm != null ? `${r.quote.ytm.toFixed(1)}%` : "—"}
                  {r.markets_closed && (
                    <span className="ml-1 text-[9px] uppercase text-rose-300">closed</span>
                  )}
                  {r.refi_prob_pct != null && (
                    <span className="ml-1 text-slate-500">/ {r.refi_prob_pct}%</span>
                  )}
                </Td>
                <Td>
                  <Badge tone={VERDICT_TONE[r.verdict] || "neutral"}>
                    {(r.verdict || "").replace(/_/g, " ")}
                  </Badge>
                  {(r.annotations || []).map((a, i) => (
                    <div key={i} className="mt-0.5 max-w-[14rem] text-[10px] text-slate-500">{a}</div>
                  ))}
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-2 text-[11px] text-slate-500">{data.derivation}</div>
    </div>
  );
}
