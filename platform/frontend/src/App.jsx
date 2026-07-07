import React, { useEffect, useRef, useState } from "react";
import {
  Navigate, NavLink, Route, Routes, useLocation, useNavigate, useParams,
} from "react-router-dom";
import { fetchHealth, setLlmEnabled } from "./api.js";
import ScreenTable from "./components/ScreenTable.jsx";
import OverviewPage from "./pages/OverviewPage.jsx";
import CapitalPage from "./pages/CapitalPage.jsx";
import RiskPage from "./pages/RiskPage.jsx";
import RecoveryPage from "./components/RecoveryPage.jsx";

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

function CompanyLayout({ years, health }) {
  const { ticker } = useParams();
  return (
    <div>
      <div className="mb-6 flex gap-1 border-b border-ink-700">
        {TABS.map((t) => (
          <NavLink
            key={t.id}
            to={`/company/${ticker}/${t.id}`}
            className={({ isActive }) =>
              `px-4 py-2 text-sm ${isActive ? "border-b-2 border-accent font-semibold text-white" : "text-slate-400 hover:text-slate-200"}`
            }
          >
            {t.label}
            <span className="ml-2 hidden text-[9px] text-slate-600 md:inline">g {t.key}</span>
          </NavLink>
        ))}
      </div>
      <Routes>
        <Route path="overview" element={<OverviewPage ticker={ticker} years={years} />} />
        <Route path="capital" element={<CapitalPage ticker={ticker} years={years} health={health} />} />
        <Route path="risk" element={<RiskPage ticker={ticker} />} />
        <Route path="recovery" element={<RecoveryPage ticker={ticker} years={years} />} />
        <Route path="*" element={<Navigate to="overview" replace />} />
      </Routes>
    </div>
  );
}

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const [query, setQuery] = useState("AAL");
  const [years, setYears] = useState(3);
  const [health, setHealth] = useState(null);
  const searchRef = useRef(null);
  const pendingG = useRef(false);

  const activeTicker = location.pathname.match(/^\/company\/([^/]+)/)?.[1] || null;

  useEffect(() => {
    fetchHealth().then(setHealth).catch(() => {});
  }, []);

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
                  title="toggle LLM analysis (covenants, OBS, subsidiaries, MD&A tone)"
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
          <form
            onSubmit={(e) => { e.preventDefault(); go(query); }}
            className="mx-auto flex max-w-6xl flex-wrap items-center gap-3 px-5 py-3"
          >
            <input
              ref={searchRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="ticker or CIK  ( / )"
              className="w-44 rounded-md border border-ink-600 bg-ink-800 px-3 py-1.5 font-mono text-sm text-slate-100 outline-none focus:border-accent"
            />
            <select
              value={years}
              onChange={(e) => setYears(Number(e.target.value))}
              className="rounded-md border border-ink-600 bg-ink-800 px-2 py-1.5 text-sm text-slate-300"
              title="filing lookback (capital structure / recovery)"
            >
              {[1, 2, 3, 5, 10].map((y) => <option key={y} value={y}>{y}y</option>)}
            </select>
            <button type="submit" className="rounded-md bg-accent px-4 py-1.5 text-sm font-semibold text-white hover:bg-accent/90">
              Open
            </button>
            {activeTicker && (
              <span className="ml-auto font-mono text-sm text-slate-500">{activeTicker}</span>
            )}
          </form>
        </header>

        <main className="mx-auto max-w-6xl px-5 py-6">
          <Routes>
            <Route path="/" element={<Landing onPick={go} />} />
            <Route path="/company/:ticker/*" element={<CompanyLayout years={years} health={health} />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

function Landing({ onPick }) {
  return (
    <div className="mx-auto max-w-2xl py-16 text-center">
      <h1 className="mb-2 text-2xl font-semibold text-slate-100">
        Given everything we know about this company, what is the true credit risk,
        where does it fail, and what is each security worth?
      </h1>
      <p className="mb-8 text-sm text-slate-500">
        Search an issuer, or open a pre-cached example. Every number traces to an EDGAR filing.
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
      <ScreenTable onPick={onPick} />
    </div>
  );
}
