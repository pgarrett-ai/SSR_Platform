import React, { lazy, Suspense, useEffect, useRef, useState } from "react";
import {
  Navigate, NavLink, Route, Routes, useLocation, useNavigate, useParams,
} from "react-router-dom";
import { fetchHealth, overviewJsonUrl, setLlmEnabled, streamOverview } from "./api.js";
import { getCached, setCached } from "./cache.js";
import { Button, ErrorCard, Input, Loading } from "./ui/index.jsx";
import ProgressLog from "./components/ProgressLog.jsx";
import ScreenTable from "./components/ScreenTable.jsx";

// Route-level code-splitting: the recharts-heavy company pages load on demand as separate
// chunks (fetched when a tab is first opened) instead of bloating the initial bundle.
const OverviewPage = lazy(() => import("./pages/OverviewPage.jsx"));
const CapitalPage = lazy(() => import("./pages/CapitalPage.jsx"));
const RiskPage = lazy(() => import("./pages/RiskPage.jsx"));
const RecoveryPage = lazy(() => import("./pages/RecoveryPage.jsx"));

const HEROES = [
  { t: "AAL", note: "airline · leases + pension" },
  { t: "ATUS", note: "cable · LME / structural" },
  { t: "TSE", note: "chemicals · restructuring" },
];

// Company tabs: id doubles as the route segment and the `g <key>` hotkey target.
const TABS = [
  { id: "overview", label: "Overview", key: "o" },
  { id: "capital", label: "Capital Structure", key: "c" },
  { id: "risk", label: "Default Risk", key: "r" },
  { id: "recovery", label: "Recovery", key: "v" },
];

function CompanyLayout({ years, health, overview }) {
  const { ticker } = useParams();
  return (
    <Suspense fallback={<Loading />}>
      <Routes>
        <Route path="overview" element={<OverviewPage ticker={ticker} years={years} />} />
        <Route
          path="capital"
          element={<CapitalPage ticker={ticker} health={health} overview={overview} />}
        />
        <Route path="risk" element={<RiskPage ticker={ticker} years={years} />} />
        <Route path="recovery" element={<RecoveryPage ticker={ticker} years={years} />} />
        <Route path="*" element={<Navigate to="overview" replace />} />
      </Routes>
    </Suspense>
  );
}

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const [query, setQuery] = useState("AAL");
  // Draft vs committed lookback: the input edits `years` freely with no side effects;
  // only the explicit Open action commits it. Everything reactive keys off appliedYears —
  // otherwise each keystroke in the number field kicked off refetches (worst case the
  // ~3-min pipeline for a disk-uncached year).
  const [years, setYears] = useState(3);
  const [appliedYears, setAppliedYears] = useState(3);
  const [health, setHealth] = useState(null);
  const searchRef = useRef(null);
  const pendingG = useRef(false);

  const activeTicker = location.pathname.match(/^\/company\/([^/]+)/)?.[1] || null;
  const onCapitalTab = /^\/company\/[^/]+\/capital/.test(location.pathname);

  // Overview pipeline state lives in the shell so Run Live (sidebar) and the progress
  // log (top of main) work from any tab; CapitalPage is purely presentational.
  const [overview, setOverview] = useState(null);
  const [ovEvents, setOvEvents] = useState([]);
  const [ovLoading, setOvLoading] = useState(false);
  const [ovError, setOvError] = useState(null);
  const streamRef = useRef(null);
  const lastKeyRef = useRef(null);
  const cacheKey = activeTicker ? `overview:${activeTicker}:${appliedYears}` : null;

  async function runOverview(live = false) {
    if (!activeTicker) return;
    streamRef.current?.cancel();
    setOvLoading(true);
    setOvError(null);
    setOvEvents([]);
    setOverview(null);
    const key = cacheKey;
    const ctrl = streamOverview(activeTicker, appliedYears, live, (e) => setOvEvents((prev) => [...prev, e]));
    streamRef.current = ctrl;
    try {
      const ov = await ctrl.promise;
      setOverview(setCached(key, ov));
    } catch (err) {
      setOvError(err.message || String(err));
    } finally {
      setOvLoading(false);
    }
  }

  useEffect(() => {
    fetchHealth().then(setHealth).catch(() => {});
  }, []);

  // One effect owns cached-vs-run: on a ticker/years change, cancel any in-flight run and
  // serve the session cache; the cached pipeline auto-runs only when the Capital tab needs
  // it (progress persists across tab switches — only a company/timeframe change cancels).
  useEffect(() => {
    if (!cacheKey) return;
    if (lastKeyRef.current !== cacheKey) {
      lastKeyRef.current = cacheKey;
      streamRef.current?.cancel();
      setOvLoading(false);
      setOvEvents([]);
      setOvError(null);
      setOverview(getCached(cacheKey) || null);
    }
    if (onCapitalTab && !getCached(cacheKey) && !ovLoading) runOverview(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cacheKey, onCapitalTab]);

  // Keyboard: "/" focuses search; "g" then o/c/r/v jumps tabs.
  useEffect(() => {
    function onKey(e) {
      const tag = document.activeElement?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === "/") {
        e.preventDefault();
        searchRef.current?.focus();
        return;
      }
      if (e.key === "g") {
        pendingG.current = true;
        setTimeout(() => (pendingG.current = false), 800);
        return;
      }
      if (pendingG.current && activeTicker) {
        const tab = TABS.find((t) => t.key === e.key);
        if (tab) navigate(`/company/${activeTicker}/${tab.id}`);
        pendingG.current = false;
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [activeTicker, navigate]);

  function go(tk) {
    const t = tk.trim().toUpperCase();
    if (!t) return;
    setQuery(t);
    setAppliedYears(years);   // Open commits the drafted lookback
    const tab = location.pathname.match(/^\/company\/[^/]+\/([^/]+)/)?.[1] || "overview";
    navigate(`/company/${t}/${tab}`);
  }

  return (
    <div className="flex min-h-screen">
      {/* Sidebar */}
      <aside className="hidden w-52 shrink-0 border-r border-ink-700 bg-ink-900/60 md:block">
        <div className="sticky top-0 p-4">
          <div className="mb-1 font-bold tracking-tight text-slate-100">◆ Distressed Credit</div>
          <div className="mb-6 text-[10px] uppercase tracking-[0.2em] text-slate-600">research platform</div>

          <form
            onSubmit={(e) => { e.preventDefault(); go(query); }}
            className="mb-6 flex flex-col gap-2"
          >
            <Input
              ref={searchRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="ticker or CIK  ( / )"
              className="w-full font-mono"
            />
            <label className="flex items-center justify-between text-[11px] text-slate-500">
              lookback (years)
              <Input
                type="number"
                min={1}
                max={20}
                value={years}
                onChange={(e) => setYears(Math.min(20, Math.max(1, Number(e.target.value) || 1)))}
                className="w-16 px-2 text-right"
              />
            </label>
            <Button type="submit" variant="primary" className="w-full">
              Open
            </Button>
          </form>

          <div className="mb-6 flex flex-col gap-2">
            <Button
              onClick={() => runOverview(true)}
              disabled={!activeTicker || ovLoading}
              title="re-run the full pipeline against EDGAR (~3 min with LLM)"
              className="w-full"
            >
              Run live ↻
            </Button>
            {overview && (
              <a
                href={overviewJsonUrl(overview.header.ticker, overview.header.years, false)}
                target="_blank"
                rel="noreferrer"
                className="text-center text-[11px] text-slate-500 hover:text-slate-300"
              >
                Download JSON
              </a>
            )}
            {health && !health.llm_enabled && (
              <div className="text-[11px] text-amber-400">
                {health.llm_key_set
                  ? "LLM analysis is off — live runs reuse the last saved analysis"
                  : "LLM key not set — OBS/covenant sections skipped"}
              </div>
            )}
          </div>

          <div className="mb-2 text-[10px] uppercase tracking-wide text-slate-600">Companies</div>
          <nav className="mb-6 flex flex-col gap-1">
            {HEROES.map((h) => (
              <button
                key={h.t}
                onClick={() => go(h.t)}
                className={`rounded-md px-2 py-1.5 text-left text-sm hover:bg-ink-700 ${activeTicker === h.t ? "bg-ink-700 text-white" : "text-slate-400"}`}
                title={h.note}
              >
                <span className="font-mono">{h.t}</span>
                <span className="ml-2 text-[10px] text-slate-600">{h.note.split("·")[0]}</span>
              </button>
            ))}
          </nav>

          <div className="space-y-1 text-[11px] text-slate-600">
            <div><kbd className="rounded bg-ink-700 px-1">/</kbd> search</div>
            <div><kbd className="rounded bg-ink-700 px-1">g</kbd>+<kbd className="rounded bg-ink-700 px-1">o c r v</kbd> tabs</div>
          </div>

          {health && (
            <div className="mt-6 flex items-center gap-2 text-[11px] text-slate-600">
              LLM:
              {health.llm_key_set ? (
                <button
                  onClick={async () => {
                    try {
                      await setLlmEnabled(!health.llm_enabled);
                      setHealth(await fetchHealth());
                    } catch {}
                  }}
                  title="toggle LLM analysis (covenants, OBS, subsidiaries)"
                  className={`rounded-full border px-2 py-0.5 ${health.llm_enabled ? "border-emerald-400/40 text-emerald-400" : "border-amber-400/40 text-amber-400"}`}
                >
                  {health.llm_enabled ? "on" : "off"}
                </button>
              ) : (
                <span className="text-amber-400" title="set ANTHROPIC_API_KEY in platform/.env">no key</span>
              )}
            </div>
          )}
        </div>
      </aside>

      {/* Main column */}
      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-10 border-b border-ink-700 bg-ink-900/80 backdrop-blur">
          <div className="mx-auto flex max-w-6xl items-center gap-1 px-5">
            {activeTicker ? (
              <>
                {TABS.map((t) => (
                  <NavLink
                    key={t.id}
                    to={`/company/${activeTicker}/${t.id}`}
                    className={({ isActive }) =>
                      `px-4 py-3 text-sm ${isActive ? "border-b-2 border-accent font-semibold text-white" : "text-slate-400 hover:text-slate-200"}`
                    }
                  >
                    {t.label}
                    <span className="ml-2 hidden text-[9px] text-slate-600 md:inline">g {t.key}</span>
                  </NavLink>
                ))}
                <span className="ml-auto font-mono text-sm text-slate-500">{activeTicker}</span>
              </>
            ) : (
              <span className="py-3 text-sm text-slate-500">Search any SEC issuer — every number traces to an EDGAR filing.</span>
            )}
          </div>
        </header>

        <main className="mx-auto max-w-6xl px-5 py-6">
          {(ovLoading || ovEvents.length > 0) && <ProgressLog events={ovEvents} done={!!overview} />}
          {ovError && (
            <ErrorCard className="mb-8">
              <span className="font-semibold">Could not complete:</span> {ovError}
            </ErrorCard>
          )}
          <Routes>
            <Route path="/" element={<Landing onPick={go} />} />
            <Route
              path="/company/:ticker/*"
              element={<CompanyLayout years={appliedYears} health={health} overview={overview} />}
            />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

function Landing({ onPick }) {
  return (
    <div className="mx-auto max-w-4xl py-12">
      <div className="mx-auto mb-4 max-w-2xl text-center">
        <h1 className="mb-2 text-2xl font-semibold text-slate-100">
          Default risk · capital structure · recovery
        </h1>
        <p className="mb-8 text-sm text-slate-500">
          Search any SEC issuer. Every number traces to an EDGAR filing.
        </p>
        <div className="flex justify-center gap-3">
          {HEROES.map((h) => (
            <button
              key={h.t}
              onClick={() => onPick(h.t)}
              className="rounded-full border border-ink-600 px-4 py-2 text-sm text-slate-300 hover:border-accent hover:text-white"
            >
              <span className="font-mono">{h.t}</span>
              <span className="ml-2 text-slate-500">{h.note}</span>
            </button>
          ))}
        </div>
      </div>
      <ScreenTable onPick={onPick} />
    </div>
  );
}
