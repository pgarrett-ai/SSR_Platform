import React, { useEffect, useRef, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { overviewJsonUrl, streamOverview } from "../api.js";
import { getCached, setCached } from "../cache.js";
import Header from "../components/Header.jsx";
import Section from "../components/Section.jsx";
import ProgressLog from "../components/ProgressLog.jsx";
import ForensicTable from "../components/ForensicTable.jsx";
import FlagCard from "../components/FlagCard.jsx";
import SourcesPanel from "../components/SourcesPanel.jsx";
import EconomicDebtBridge from "../components/EconomicDebtBridge.jsx";
import DebtScheduleTable from "../components/DebtScheduleTable.jsx";
import ObsFindings from "../components/ObsFindings.jsx";
import XbrlTieOut from "../components/XbrlTieOut.jsx";
import SubsidiariesList from "../components/SubsidiariesList.jsx";
import CovenantCard from "../components/CovenantCard.jsx";
import MdnaDrift from "../components/MdnaDrift.jsx";

// The original CapStack page, extracted from the pre-router App. Loads automatically for
// the routed ticker (cache-first: session cache here, snapshot cache server-side).

// Phase 4.6: face due per calendar year, parsed from footnote maturity strings
// (ranges like "2026 to 2038" are spread evenly — hover shows the instruments).
function MaturityWall({ wall }) {
  const data = wall.map((b) => ({
    year: b.year,
    face: +(b.face / 1e9).toFixed(2),
    instruments: b.instruments.join(", "),
  }));
  return (
    <div className="mt-5">
      <div className="mb-1 text-xs text-slate-500">Maturity wall — face due per year ($B)</div>
      <ResponsiveContainer width="100%" height={160}>
        <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
          <CartesianGrid stroke="#263041" strokeDasharray="3 3" />
          <XAxis dataKey="year" tick={{ fill: "#94a3b8", fontSize: 10 }} />
          <YAxis tick={{ fill: "#94a3b8", fontSize: 10 }} />
          <Tooltip
            contentStyle={{ background: "#111827", border: "1px solid #263041", fontSize: 11 }}
            formatter={(v, _n, p) => [`$${v}B — ${p.payload.instruments}`, null]}
          />
          <Bar dataKey="face" fill="#5e7bff" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function CapitalPage({ ticker, years, health }) {
  const [events, setEvents] = useState([]);
  const [overview, setOverview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const streamRef = useRef(null);

  const cacheKey = `overview:${ticker}:${years}`;

  async function run(live = false) {
    streamRef.current?.cancel();
    setLoading(true);
    setError(null);
    setEvents([]);
    setOverview(null);
    const ctrl = streamOverview(ticker, years, live, (e) => setEvents((prev) => [...prev, e]));
    streamRef.current = ctrl;
    try {
      const ov = await ctrl.promise;
      setOverview(setCached(cacheKey, ov));
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const cached = getCached(cacheKey);
    if (cached) {
      setOverview(cached);
      setEvents([]);
      setError(null);
      return;
    }
    run(false);
    return () => streamRef.current?.cancel();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cacheKey]);

  const flags = overview?.forensic_flags || [];

  return (
    <div>
      <div className="mb-4 flex items-center gap-3 text-xs text-slate-500">
        <button
          onClick={() => run(true)}
          disabled={loading}
          className="rounded-md border border-ink-600 px-3 py-1.5 text-slate-300 hover:border-accent hover:text-white disabled:opacity-50"
          title="bypass all caches and re-run the pipeline against EDGAR (~3 min with LLM)"
        >
          Run live ↻
        </button>
        {overview && (
          <a
            href={overviewJsonUrl(overview.header.ticker, overview.header.years, false)}
            target="_blank"
            rel="noreferrer"
            className="rounded-md border border-ink-600 px-3 py-1.5 text-slate-300 hover:border-accent hover:text-white"
          >
            Download JSON
          </a>
        )}
        {health && !health.llm_enabled && (
          <span className="text-amber-400">LLM key not set — OBS/covenant sections skipped</span>
        )}
      </div>

      {(loading || events.length > 0) && <ProgressLog events={events} done={!!overview} />}

      {error && (
        <div className="mb-8 rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 text-sm text-rose-200">
          <span className="font-semibold">Could not complete:</span> {error}
        </div>
      )}

      {overview && (
        <>
          <Header header={overview.header} />

          {overview.warnings?.length > 0 && (
            <div className="mb-8 rounded-xl border border-amber-500/30 bg-amber-500/5 p-3 text-[13px] text-amber-200/90">
              {overview.warnings.map((w, i) => (
                <div key={i}>⚠ {w}</div>
              ))}
            </div>
          )}

          <Section
            title="Economic Debt Bridge"
            subtitle="reported debt → economic (adjusted) debt"
            badge={overview.header.llm_enabled ? null : "needs API key"}
          >
            <EconomicDebtBridge bridge={overview.economic_debt_bridge} />
          </Section>

          <Section
            title="As-reported debt schedule"
            subtitle={`${overview.debt_schedule?.length || 0} instruments from the debt footnote`}
          >
            <DebtScheduleTable instruments={overview.debt_schedule} />
            {overview.maturity_wall?.length > 0 && <MaturityWall wall={overview.maturity_wall} />}
          </Section>

          {overview.xbrl_tie_outs?.length > 0 && (
            <Section
              title="XBRL tie-out"
              subtitle="footnote totals reconciled against XBRL — the v1 confidence score"
            >
              <XbrlTieOut tieOuts={overview.xbrl_tie_outs} />
            </Section>
          )}

          <Section title="Forensic cash-vs-debt test" subtitle="where is the cash coming from?">
            <ForensicTable rows={overview.forensic_table} />
            {flags.length > 0 && (
              <div className="mt-5 grid gap-3 md:grid-cols-2">
                {flags.map((f, i) => (
                  <FlagCard key={i} flag={f} />
                ))}
              </div>
            )}
          </Section>

          <Section
            title="Off-balance-sheet findings"
            subtitle={`${overview.obs_items?.length || 0} items extracted from footnotes & MD&A`}
          >
            <ObsFindings items={overview.obs_items} />
          </Section>

          {overview.subsidiaries?.length > 0 && (
            <Section
              title="Legal entities (Exhibit 21)"
              subtitle={`${overview.subsidiaries.length} subsidiaries — seeds Fulcrum's entity tree`}
            >
              <SubsidiariesList subsidiaries={overview.subsidiaries} />
            </Section>
          )}

          <Section
            title="Covenant summary"
            subtitle={`${overview.covenants?.length || 0} agreement(s) from EX-10.x / EX-4.x`}
          >
            <CovenantCard covenants={overview.covenants} />
          </Section>

          <Section title="MD&A semantic drift" badge="experimental" subtitle={`${overview.mdna_drift?.length || 0} periods`}>
            <MdnaDrift points={overview.mdna_drift} />
          </Section>

          <Section title="Sources" subtitle={`${overview.sources.length} filings analyzed`}>
            <SourcesPanel sources={overview.sources} />
          </Section>
        </>
      )}
    </div>
  );
}
