import React, { lazy, Suspense, useEffect, useRef, useState } from "react";
import {
  Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate, useParams,
} from "react-router-dom";
import { fetchHealth, getToken, setToken, streamHazard, streamOverview } from "./api.js";
import { useStreamed } from "./cache.js";
import { Input, Loading } from "./ui/index.jsx";
import ScreenTable from "./components/ScreenTable.jsx";

// Route-level code-splitting: the recharts-heavy company pages load on demand as separate
// chunks (fetched when a tab is first opened) instead of bloating the initial bundle.
const CapitalPage = lazy(() => import("./pages/CapitalPage.jsx"));
const RiskPage = lazy(() => import("./pages/RiskPage.jsx"));
const RecoveryPage = lazy(() => import("./pages/RecoveryPage.jsx"));

const HEROES = [
  { t: "AAL", note: "airline · leases + pension" },
  { t: "ATUS", note: "cable · LME / structural" },
  { t: "TSE", note: "chemicals · restructuring" },
];

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
  const overview = useStreamed(`overview:${t}:${OVERVIEW_YEARS}`,
    (onProgress) => streamOverview(t, OVERVIEW_YEARS, false, onProgress).promise);
  const hazard = useStreamed(`hazard:${t}`,
    (onProgress) => streamHazard(t, HAZARD_YEARS, onProgress).promise);

  return (
    <Suspense fallback={<Loading />}>
      <Routes>
        <Route path="capital" element={<CapitalPage ticker={t} overview={overview} />} />
        <Route path="risk" element={<RiskPage ticker={t} hazard={hazard} />} />
        <Route path="recovery" element={<RecoveryPage ticker={t} years={OVERVIEW_YEARS} />} />
        <Route path="*" element={<Navigate to={`/company/${t}/capital`} replace />} />
      </Routes>
    </Suspense>
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
          <Route path="/" element={<Landing onPick={go} />} />
          <Route path="/company/:ticker/*" element={<CompanyLayout />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
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
