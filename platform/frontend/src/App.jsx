import React, { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import {
  Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate, useParams,
} from "react-router-dom";
import { fetchEvents, fetchHealth, getToken, setToken, streamHazard, streamOverview } from "./api.js";
import { useAsync, useStreamed } from "./cache.js";
import { Input, Loading, Section } from "./ui/index.jsx";
import IssuerStrip from "./components/IssuerStrip.jsx";
import ScreenTable from "./components/ScreenTable.jsx";

// Route-level code-splitting: the recharts-heavy company pages load on demand as separate
// chunks (fetched when a tab is first opened) instead of bloating the initial bundle.
const CapitalPage = lazy(() => import("./pages/CapitalPage.jsx"));
const RiskPage = lazy(() => import("./pages/RiskPage.jsx"));
const RecoveryPage = lazy(() => import("./pages/RecoveryPage.jsx"));

// Company tabs; the id doubles as the route segment.
const TABS = [
  { id: "capital", label: "Capital Structure" },
  { id: "risk", label: "Default Risk" },
  { id: "recovery", label: "Recovery" },
];

// The overview family is read at a fixed 3-year lookback; hazard wants 10 years of history.
const OVERVIEW_YEARS = 3;
const HAZARD_YEARS = 10;

// Owns both pipeline streams for the routed ticker, so every tab reads the same run and a
// tab switch never restarts one. Neither stream blocks rendering of the other's consumers.
function CompanyLayout() {
  const t = useParams().ticker.toUpperCase();
  const tab = useLocation().pathname.split("/")[3];
  const overview = useStreamed(`overview:${t}:${OVERVIEW_YEARS}`,
    (onProgress) => streamOverview(t, OVERVIEW_YEARS, false, onProgress));
  const hazard = useStreamed(`hazard:${t}`,
    (onProgress) => streamHazard(t, HAZARD_YEARS, onProgress));

  // The RECOVER cell reads the persisted last run until Recovery reports a fresh one
  // (same shape as overview.recovery_summary: {mode, saved_at, fulcrum, tranches:
  // [{tranche, mean_recovery_pct, is_fulcrum}]}), so the strip and the tranche table agree;
  // a fresh run with nothing to report (null) hides the cell. Tagged with its ticker so a
  // switch never shows the previous issuer's run.
  // keyed by ticker: a run from the previous issuer that resolves late cannot overwrite this one
  const [live, setLive] = useState({});
  const onRecoveryResult = useCallback((summary) => setLive((m) => ({ ...m, [t]: summary })), [t]);
  const recovery = t in live ? live[t] : overview.data?.recovery_summary;

  useEffect(() => {
    document.title = `${t} — Distressed Credit`;
    return () => { document.title = "Distressed Credit"; };
  }, [t]);

  return (
    <>
      <IssuerStrip ticker={t} tab={tab} overview={overview} hazard={hazard} recovery={recovery} />
      <Suspense fallback={<Loading />}>
        <Routes>
          <Route path="capital" element={<CapitalPage ticker={t} overview={overview} />} />
          <Route path="risk" element={
            <RiskPage ticker={t} hazard={hazard} chapter11={overview.data?.chapter11}
              overviewReady={!!overview.data} />} />
          {/* keyed by ticker: a switch remounts it, so no stale run lands on the new issuer */}
          <Route path="recovery" element={
            <RecoveryPage key={t} ticker={t} years={OVERVIEW_YEARS} onRecoveryResult={onRecoveryResult} />} />
          <Route path="*" element={<Navigate to={`/company/${t}/capital`} replace />} />
        </Routes>
      </Suspense>
    </>
  );
}

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const [query, setQuery] = useState("");
  const [health, setHealth] = useState(null);
  const searchRef = useRef(null);

  const [, , activeTicker, activeTab] = location.pathname.split("/");
  const onCompany = location.pathname.startsWith("/company/") && !!activeTicker;

  useEffect(() => {
    fetchHealth().then(setHealth).catch(() => {});
  }, []);

  // "/" focuses the ticker search from anywhere outside a form field.
  useEffect(() => {
    function onKey(e) {
      const tag = document.activeElement?.tagName;
      if (e.key !== "/" || tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      e.preventDefault();
      searchRef.current?.focus();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function go(tk) {
    const t = tk.trim().toUpperCase();
    if (!t) return;
    const tab = onCompany && TABS.some((x) => x.id === activeTab) ? activeTab : "capital";
    navigate(`/company/${t}/${tab}`);
    setQuery("");
    searchRef.current?.blur();
  }

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-ink-700 bg-ink-900/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 px-5">
          <Link to="/" className="py-3 font-bold tracking-tight text-slate-100">
            ◆ Distressed Credit
          </Link>
          {onCompany && (
            <nav className="order-last flex w-full overflow-x-auto md:order-none md:w-auto">
              {TABS.map((tab) => (
                <NavLink
                  key={tab.id}
                  to={`/company/${activeTicker}/${tab.id}`}
                  className={({ isActive }) =>
                    `whitespace-nowrap border-b-2 px-3 py-3 text-sm ${isActive
                      ? "border-accent font-semibold text-white"
                      : "border-transparent text-slate-400 hover:text-slate-200"}`
                  }
                >
                  {tab.label}
                </NavLink>
              ))}
            </nav>
          )}
          <form
            onSubmit={(e) => { e.preventDefault(); go(query); }}
            className="ml-auto flex items-center gap-2 py-2"
          >
            {health?.auth_required && (
              <Input
                type="password"
                defaultValue={getToken()}
                onBlur={(e) => setToken(e.target.value.trim())}
                placeholder="API token"
                aria-label="API token"
                className="w-32 font-mono"
              />
            )}
            <Input
              ref={searchRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Ticker or CIK"
              aria-label="Open a ticker or CIK"
              className="w-40 font-mono uppercase placeholder:normal-case"
            />
          </form>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-5 py-6">
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/company/:ticker/*" element={<CompanyLayout />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

function Landing() {
  return (
    <>
      <Section title="Analyzed issuers" subtitle="Every number traces to an EDGAR filing.">
        <ScreenTable />
      </Section>
      <RecentEvents />
    </>
  );
}

// Severe events across every issuer in the event store; absent when there are none.
function RecentEvents() {
  const since = new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10);
  const { data } = useAsync(`events:recent:${since}`,
    () => fetchEvents({ min_severity: 4, since, limit: 15 }), [since]);
  const rows = [...(data?.events || [])]
    .sort((a, b) => (b.occurred_at || "").localeCompare(a.occurred_at || ""));
  if (!rows.length) return null;

  return (
    <Section title="Recent distress events" subtitle="severity 4–5 · last 30 days">
      <ul>
        {rows.map((e) => (
          <li key={e.id} className="flex items-baseline gap-3 border-b border-ink-700/40 py-1.5 text-sm last:border-0">
            <span className="w-24 shrink-0 font-mono text-xs text-slate-400">
              {e.occurred_at?.slice(0, 10) || "—"}
            </span>
            <span className="w-20 shrink-0 font-mono text-xs">
              {e.ticker ? (
                <Link to={`/company/${e.ticker}/capital`} className="text-slate-200 hover:text-accent">
                  {e.ticker}
                </Link>
              ) : (
                <span className="text-slate-500">CIK {Number(e.cik)}</span>
              )}
            </span>
            {e.source_url ? (
              <a href={e.source_url} target="_blank" rel="noreferrer"
                className="truncate text-slate-300 hover:text-accent">
                {e.title || e.event_type}
              </a>
            ) : (
              <span className="truncate text-slate-300">{e.title || e.event_type}</span>
            )}
          </li>
        ))}
      </ul>
    </Section>
  );
}
