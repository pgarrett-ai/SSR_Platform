import React from "react";
import { ErrorCard, Loading } from "../ui/index.jsx";
import ProgressLog from "../components/ProgressLog.jsx";
import RiskTiles from "../components/risk/RiskTiles.jsx";
import MarketPanel from "../components/risk/MarketPanel.jsx";
import RiskTimeline from "../components/risk/RiskTimeline.jsx";
import EightKEvents from "../components/risk/EightKEvents.jsx";
import RestatementScreen from "../components/RestatementScreen.jsx";

// Renders the hazard stream CompanyLayout owns; the 12m PD headline sits in the strip.
export default function RiskPage({ ticker, hazard }) {
  const { data, loading, error, events } = hazard;
  if (loading)
    return events.length > 0
      ? <ProgressLog events={events} />
      : <Loading>Loading market and filing data…</Loading>;
  if (error) return <ErrorCard>{error}</ErrorCard>;
  if (!data) return null;

  return (
    <div>
      {data.stale_as_of && (
        <p className="mb-4 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
          Showing the last saved analysis from {String(data.stale_as_of).slice(0, 10)}: the live
          market and EDGAR refresh failed.
        </p>
      )}
      <RiskTiles data={data} />
      <MarketPanel data={data} />
      <RiskTimeline data={data} />
      <RestatementScreen ticker={ticker} years={3} />
      <EightKEvents ticker={ticker} />
    </div>
  );
}
