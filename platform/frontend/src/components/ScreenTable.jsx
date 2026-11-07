import React, { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { fetchScreen } from "../api.js";
import { EmptyState, Skeleton, Td, Th, fmtLev, rowClass, useSort } from "../ui/index.jsx";

// One-line thesis per showcase name, shown under the issuer.
const NOTES = {
  AAL: "airline · leases + pension",
  ATUS: "cable · LME / structural",
  TSE: "chemicals · Chapter 11 + DIP",
  LCID: "EV maker · cash burn, PIF-backed",
};

const NM = <span className="text-slate-500" title="not meaningful — EBITDA ≤ 0">n.m.</span>;
const runwayTone = (m) => (m < 6 ? "text-rose-300" : m < 12 ? "text-amber-300" : "text-slate-300");

export default function ScreenTable() {
  const navigate = useNavigate();
  const [state, setState] = useState({ rows: null, error: null });
  useEffect(() => {
    fetchScreen().then(
      (rows) => setState({ rows, error: null }),
      (e) => setState({ rows: [], error: e.message }),
    );
  }, []);

  // Leverage is null both when EBITDA ≤ 0 and when nothing was extracted; n.m. sorts as
  // the most levered so the distressed names lead the default sort.
  const rows = useMemo(() => (state.rows || []).map((r) => ({
    ...r,
    _lev: r.economic_leverage ?? (r.ebitda != null && r.ebitda <= 0 ? Infinity : null),
  })), [state.rows]);
  const { sorted, thProps } = useSort(rows, "_lev");

  if (!state.rows) return <Skeleton rows={4} />;
  if (state.error) return <EmptyState hint={state.error}>The screen could not be loaded.</EmptyState>;
  if (!rows.length)
    return <EmptyState hint="Search a ticker above to run the first analysis.">No issuers analyzed yet.</EmptyState>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-ink-600">
            <Th {...thProps("ticker")}>Ticker</Th>
            <Th {...thProps("issuer")}>Issuer</Th>
            <Th right {...thProps("_lev")} title="economic debt ÷ EBITDA">Economic lev</Th>
            <Th right {...thProps("runway_months")} title="months of total liquidity (cash, short-term investments, undrawn credit) at the current burn">Runway (mo)</Th>
            <Th right {...thProps("flag_count")}>Flags</Th>
            <Th right {...thProps("last_updated")}>As of</Th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr
              key={r.ticker}
              // the ticker cell is a real link (cmd-click opens a tab); the rest of the row follows it
              onClick={(e) => !e.target.closest("a") && navigate(`/company/${r.ticker}/capital`)}
              className={`cursor-pointer ${rowClass}`}
            >
              <Td mono>
                <Link to={`/company/${r.ticker}/capital`} className="text-slate-100 hover:text-accent">
                  {r.ticker}
                </Link>
              </Td>
              <Td>
                <div className="text-slate-300">{r.issuer || "—"}</div>
                {NOTES[r.ticker] && <div className="text-[11px] text-slate-400">{NOTES[r.ticker]}</div>}
              </Td>
              <Td right mono className="text-slate-300">{r._lev === Infinity ? NM : fmtLev(r._lev)}</Td>
              <Td right mono className={r.runway_months == null ? "text-slate-500" : runwayTone(r.runway_months)}>
                {r.runway_months == null ? "—" : r.runway_months > 60 ? "60+" : r.runway_months.toFixed(0)}
              </Td>
              <Td right mono className="text-slate-400">{r.flag_count ?? "—"}</Td>
              <Td right mono className="text-[12px] text-slate-400">{r.last_updated?.slice(0, 10) || "—"}</Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
