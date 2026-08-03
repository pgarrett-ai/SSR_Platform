import React, { useEffect, useRef, useState } from "react";
import { streamHazard } from "../api.js";
import { useAsync } from "../cache.js";
import { Badge, ErrorCard, Loading, ZONE_COLOR, fmtPct, fmtNum } from "../ui/index.jsx";
import ProgressLog from "../components/ProgressLog.jsx";
import ExecutiveSummary from "../components/risk/ExecutiveSummary.jsx";
import RiskTimeline from "../components/risk/RiskTimeline.jsx";
import Contributions from "../components/risk/Contributions.jsx";
import Financials from "../components/risk/Financials.jsx";
import MarketPanel from "../components/risk/MarketPanel.jsx";
import EventTimeline from "../components/risk/EventTimeline.jsx";
import SurvivalPanel from "../components/risk/SurvivalPanel.jsx";
import RestatementScreen from "../components/RestatementScreen.jsx";

// The hazard dashboard, ported from hazard/frontend (its header/search now lives in the shell).
// Uses a 10y lookback regardless of the topbar setting — the risk timeline wants history.

const HAZARD_YEARS = 10;

function ScoreChip({ name, sc }) {
  if (!sc?.available)
    return <div className="rounded-lg bg-ink-700 px-3 py-2 text-xs text-slate-500">{name}: n/a</div>;
  let body;
  if (name.startsWith("Altman")) {
    body = <span style={{ color: ZONE_COLOR[sc.zone] }}>{fmtNum(sc.value)} · {sc.zone}</span>;
  } else {
    body = (
      <span className="text-slate-100">
        {fmtPct(sc.value, 2)} PD{sc.experimental ? " *" : ""}
        {sc.implied_rating && <span className="text-slate-400"> ≈ {sc.implied_rating}</span>}
      </span>
    );
  }
  return (
    <div className="rounded-lg bg-ink-700 px-3 py-2 text-xs" title={sc.note || undefined}>
      <span className="text-slate-500">{name}: </span>
      {body}
      {sc.trained && !sc.real_labels && <Badge tone="watch" className="ml-1.5">demo</Badge>}
    </div>
  );
}

export default function RiskPage({ ticker, years }) {
  // Streamed load with a live progress log (same SSE pattern as the Capital pipeline).
  // The session-cache key stays shared with the Overview hazard card, so whichever
  // page loads first makes the other instant.
  const [events, setEvents] = useState([]);
  const ctrlRef = useRef(null);
  const { data, loading, error } = useAsync(
    `hazard:${ticker}`,
    () => {
      setEvents([]);
      const ctrl = streamHazard(ticker, HAZARD_YEARS, (e) => setEvents((prev) => [...prev, e]));
      ctrlRef.current = ctrl;
      return ctrl.promise;
    },
    [ticker],
  );
  useEffect(() => () => ctrlRef.current?.cancel(), [ticker]);

  if (loading)
    return events.length > 0 ? (
      <ProgressLog events={events} done={false} />
    ) : (
      <Loading>Pulling EDGAR + market data for {ticker}…</Loading>
    );
  if (error) return <ErrorCard>{error}</ErrorCard>;
  if (!data) return null;

  return (
    <div>
      <div className="mb-2 flex items-baseline gap-2">
        <h1 className="text-xl font-semibold text-slate-100">{data.issuer?.name}</h1>
        <span className="font-mono text-sm text-slate-500">
          {data.issuer?.ticker} · CIK {data.issuer?.cik}
        </span>
      </div>
      <ExecutiveSummary data={data} />
      <div className="my-4 flex flex-wrap gap-2">
        {Object.entries(data.scores || {})
          .filter(([name]) => !name.startsWith("Merton"))   // DD/PD already in the summary tiles
          .map(([name, sc]) => (
            <ScoreChip key={name} name={name} sc={sc} />
          ))}
        {data.cross_signals?.hidden_leverage && (
          <div className="rounded-lg bg-ink-700 px-3 py-2 text-xs" title={data.cross_signals.hidden_leverage.source}>
            <span className="text-slate-500">Hidden leverage: </span>
            <span className="text-slate-100">{fmtNum(data.cross_signals.hidden_leverage.raw)}x</span>
            <span className="text-slate-500"> → risk {Math.round(data.cross_signals.hidden_leverage.risk)}</span>
          </div>
        )}
      </div>
      <RiskTimeline data={data} />
      <Contributions data={data} />
      <Financials data={data} />
      <MarketPanel data={data} />
      <EventTimeline data={data} />
      {data.survival?.available && <SurvivalPanel data={data.survival} />}
      <RestatementScreen ticker={ticker} years={years} />
    </div>
  );
}
