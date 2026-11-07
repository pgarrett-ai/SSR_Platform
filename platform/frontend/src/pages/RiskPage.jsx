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
      <RiskTiles data={data} />
      <MarketPanel data={data} />
      <RiskTimeline data={data} />
      <RestatementScreen ticker={ticker} years={3} />
      <EightKEvents ticker={ticker} />
    </div>
  );
}
