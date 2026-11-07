import React from "react";
import { fetchRefiWall } from "../api.js";
import { useAsync } from "../cache.js";
import CitedNumber from "./CitedNumber.jsx";
import { Badge, Skeleton, Td, Th } from "../ui/index.jsx";

// Per maturity year: how much of the wall cash, undrawn credit and the cash sweep repay
// in sequence, what is left to refinance, and whether the market would take it.

const VERDICT = {
  fundable: { tone: "ok", label: "covered internally" },
  refi_needed: { tone: "watch", label: "needs refinancing" },
  unrefinanceable: { tone: "high", label: "market closed" },
};

export default function RefiWall({ ticker, years }) {
  const { data, loading, error } = useAsync(
    `refi:${ticker}:${years}`, () => fetchRefiWall(ticker, years), [ticker, years]);

  if (loading) return <Skeleton rows={3} />;
  if (error) return <div className="text-xs text-rose-300">{error}</div>;
  if (!data?.available)
    return <div className="text-sm text-slate-400">No dated maturities to sequence.</div>;

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-ink-600">
              <Th>Year</Th>
              <Th right>Face</Th>
              <Th>Instruments</Th>
              <Th right title="repaid in sequence from cash + undrawn credit + the cash sweep, after earlier years">
                Repayable internally
              </Th>
              <Th right title="face − repayable internally">Refi need</Th>
              <Th>Verdict</Th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r) => {
              const v = VERDICT[r.verdict] || { tone: "neutral", label: r.verdict };
              return (
                <tr key={r.year} className="border-b border-ink-700/60 align-top">
                  <Td mono className="text-slate-200">{r.year}</Td>
                  <Td right mono><CitedNumber cv={r.face} className="text-slate-200" /></Td>
                  <Td className="max-w-[18rem] truncate text-xs text-slate-400"
                    title={(r.instruments || []).join(", ")}>
                    {(r.instruments || []).join(", ")}
                  </Td>
                  <Td right mono><CitedNumber cv={r.repayable} className="text-slate-300" /></Td>
                  <Td right mono>
                    <CitedNumber cv={r.refi_need}
                      className={r.refi_need?.value > 0 ? "text-amber-300" : "text-slate-400"} />
                  </Td>
                  <Td>
                    <Badge tone={v.tone} title={(r.annotations || []).join(" · ") || undefined}>
                      {v.label}
                    </Badge>
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {(data.notes || []).map((n, i) => (
        <p key={i} className="mt-1 text-[11px] text-slate-400">{n}</p>
      ))}
    </div>
  );
}
