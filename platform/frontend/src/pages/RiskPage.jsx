import React, { useEffect, useState } from "react";
import { fetchHazard } from "../api.js";
import { getCached, setCached } from "../cache.js";
import { Card, ZONE_COLOR, fmtPct, fmtNum } from "../components/risk/ui.jsx";
import ExecutiveSummary from "../components/risk/ExecutiveSummary.jsx";
import RiskTimeline from "../components/risk/RiskTimeline.jsx";
import Contributions from "../components/risk/Contributions.jsx";
import HealthRadar from "../components/risk/HealthRadar.jsx";
import Financials from "../components/risk/Financials.jsx";
import MarketPanel from "../components/risk/MarketPanel.jsx";
import EventTimeline from "../components/risk/EventTimeline.jsx";

// The hazard dashboard, ported from hazard/frontend (its header/search now lives in the shell).
// Uses a 10y lookback regardless of the topbar setting — the risk timeline wants history.

const HAZARD_YEARS = 10;

function ScoreChip({ name, sc }) {
  if (!sc?.available)
    return <div className="rounded-lg bg-ink-700 px-3 py-2 text-xs text-slate-500">{name}: n/a</div>;
  let body;
  if (name.startsWith("Altman")) {
    body = <span style={{ color: ZONE_COLOR[sc.zone] }}>{fmtNum(sc.value)} · {sc.zone}</span>;
  } else if (name.startsWith("Merton")) {
    body = <span className="text-slate-100">{fmtNum(sc.value)}σ DD</span>;
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
      {sc.real_labels && <span className="ml-1.5 rounded bg-emerald-500/15 px-1 py-0.5 text-[9px] uppercase text-emerald-300">real labels</span>}
      {sc.trained && !sc.real_labels && <span className="ml-1.5 rounded bg-amber-500/15 px-1 py-0.5 text-[9px] uppercase text-amber-300">demo</span>}
    </div>
  );
}

export default function RiskPage({ ticker }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const cacheKey = `hazard:${ticker}`;

  useEffect(() => {
    const cached = getCached(cacheKey);
    if (cached) {
      setData(cached);
      return;
    }
    setLoading(true);
    setError(null);
    setData(null);
    fetchHazard(ticker, HAZARD_YEARS)
      .then((d) => setData(setCached(cacheKey, d)))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [cacheKey, ticker]);

  if (loading)
    return (
      <div className="py-16 text-center text-sm text-slate-400">
        Pulling EDGAR + market data for {ticker}… (a first run can take ~30–60s)
      </div>
    );
  if (error)
    return <Card className="border-rose-500/40 bg-rose-500/10 text-sm text-rose-200">{error}</Card>;
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
        {Object.entries(data.scores || {}).map(([name, sc]) => (
          <ScoreChip key={name} name={name} sc={sc} />
        ))}
        {Object.entries(data.cross_signals || {}).map(([key, cs]) => (
          <div key={key} className="rounded-lg bg-ink-700 px-3 py-2 text-xs" title={cs.source}>
            <span className="text-slate-500">{key === "hidden_leverage" ? "Hidden leverage" : "MD&A tone"}: </span>
            <span className="text-slate-100">{fmtNum(cs.raw)}{key === "hidden_leverage" ? "x" : ""}</span>
            <span className="text-slate-500"> → risk {Math.round(cs.risk)}</span>
          </div>
        ))}
      </div>
      <RiskTimeline data={data} />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Contributions data={data} />
        <HealthRadar data={data} />
      </div>
      <Financials data={data} />
      <MarketPanel data={data} />
      <EventTimeline data={data} />
      <p className="mt-6 text-xs text-slate-600">
        Scores from published-coefficient models (Altman Z″, Merton, CHS). * CHS is an
        experimental point-in-time approximation.
        {data.scores?.["Trained hazard"]?.real_labels && (
          <> Trained hazard: fitted on real EDGAR 8-K Item 1.03 bankruptcy labels,
          walk-forward validated — hover the chip for provenance.</>
        )}{" "}
        Not investment advice.
      </p>
    </div>
  );
}
