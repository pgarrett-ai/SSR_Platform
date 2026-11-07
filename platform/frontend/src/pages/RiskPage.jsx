import React, { useEffect, useRef, useState } from "react";
import { streamHazard } from "../api.js";
import { useAsync } from "../cache.js";
import { ErrorCard, Loading } from "../ui/index.jsx";
import ProgressLog from "../components/ProgressLog.jsx";
import RiskTiles from "../components/risk/RiskTiles.jsx";
import MarketPanel from "../components/risk/MarketPanel.jsx";
import RiskTimeline from "../components/risk/RiskTimeline.jsx";
import EightKEvents from "../components/risk/EightKEvents.jsx";
import RestatementScreen from "../components/RestatementScreen.jsx";

// The hazard dashboard, ported from hazard/frontend (its header/search now lives in the shell).
// Uses a 10y lookback regardless of the topbar setting — the risk timeline wants history.

const HAZARD_YEARS = 10;

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
      <RiskTiles data={data} />
      <MarketPanel data={data} />
      <RiskTimeline data={data} />
      <RestatementScreen ticker={ticker} years={years} />
      <EightKEvents ticker={ticker} />
    </div>
  );
}
