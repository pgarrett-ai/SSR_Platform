import React, { useEffect, useMemo, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";
import CitedNumber from "../components/CitedNumber.jsx";
import {
  ACCENT, Button, ErrorCard, INK, Input, LINE_COLORS, Loading, RISK, Section, Th,
  chartTooltipStyle, fmt,
} from "../ui/index.jsx";
import {
  deleteScenario, fetchLadder, fetchRecoveryStructure, listScenarios, saveScenario,
  simulateRecovery,
} from "../api.js";
import EvExplorer from "../components/EvExplorer.jsx";
import ExchangeAnalyzer from "../components/ExchangeAnalyzer.jsx";
import IrrMatrix from "../components/IrrMatrix.jsx";
import LiquidationPanel from "../components/LiquidationPanel.jsx";

// Provenance marker: § next to a tranche pops the filing citation behind its face amount.
function CiteMark({ citation }) {
  if (!citation) return null;
  return <CitedNumber cv={{ display: "§", citation }} className="text-accent" />;
}

// Port of fulcrum's Streamlit app (structure editor + simulation + results + scenarios).
// The SEC-filings browser tab moves to the Documents page in Phase 2.

const SIM_DEFAULTS = {
  base_ebitda: 100, horizon_years: 1.5, ebitda_vol: 0.28, mean_reversion: 0.6,
  stress_prob: 0.3, stress_vol: 0.55, stress_log_drift: -0.35, base_multiple: 6.0,
  distress_multiple: 4.5, multiple_vol: 0.18, corr: 0.55,
  accrual_years: null,   // empty = derived from the petition date; a number overrides
  n_draws: 50000, seed: 42,
};

// ponytail: dense table-cell inputs stay local — kit Input's px-3 py-1.5 text-sm can't be
// reliably overridden with conflicting utilities. Backgrounds track the kit (bg-ink-800).
function NumCell({ value, onChange, step = 1, className = "" }) {
  return (
    <input
      type="number"
      step={step}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
      className={`w-24 rounded border border-ink-600 bg-ink-800 px-2 py-1 font-mono text-xs text-slate-100 outline-none focus:border-accent ${className}`}
    />
  );
}

function TextCell({ value, onChange, className = "" }) {
  return (
    <input
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value)}
      className={`w-full rounded border border-ink-600 bg-ink-800 px-2 py-1 text-xs text-slate-100 outline-none focus:border-accent ${className}`}
    />
  );
}

function Field({ label, children }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-wide text-slate-500">{label}</span>
      {children}
    </label>
  );
}

function NumField({ label, value, onChange, step }) {
  return (
    <Field label={label}>
      <input
        type="number"
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-28 rounded-md border border-ink-600 bg-ink-800 px-2 py-1.5 font-mono text-xs text-slate-100 outline-none focus:border-accent"
      />
    </Field>
  );
}

export default function RecoveryPage({ ticker, years }) {
  const [structure, setStructure] = useState(null); // {name, entities, tranches, admin_fees}
  const [source, setSource] = useState(null);
  const [citations, setCitations] = useState({});   // tranche name -> filing citation
  const [availableEntities, setAvailableEntities] = useState([]);   // from Exhibit 21
  const [sim, setSim] = useState(SIM_DEFAULTS);
  const [result, setResult] = useState(null);
  const [scenarios, setScenarios] = useState([]);
  const [scenarioName, setScenarioName] = useState("");
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);
  const [quotedByName, setQuotedByName] = useState({});   // instrument -> drop-file quote
  const [petitionDate, setPetitionDate] = useState(new Date().toISOString().slice(0, 10));
  const [attack, setAttack] = useState(null);              // priority-attack scenario
  const [suggestedClaims, setSuggestedClaims] = useState(null);
  const [suggestedMezz, setSuggestedMezz] = useState(null);  // temporary equity ($mm)
  const [suggestedPriming, setSuggestedPriming] = useState(null); // liens-headroom pre-seed
  const [primingFace, setPrimingFace] = useState(null);      // priming layer face ($mm)

  useEffect(() => {
    if (!ticker) return;
    fetchLadder(ticker, years)
      .then((d) => setQuotedByName(d.quote_by_instrument || {}))
      .catch(() => setQuotedByName({}));
  }, [ticker, years]);

  useEffect(() => {
    if (!ticker) return;
    setLoading(true);
    setError(null);
    setResult(null);
    fetchRecoveryStructure(ticker, years)
      .then((d) => {
        // UI defaults 7% estate costs (Moyer: outsiders underestimate); engine default is 0
        setStructure({ ...d.structure, admin_pct: d.structure.admin_pct || 0.07 });
        setSource(d.source);
        setCitations(d.citations || {});
        setAvailableEntities(d.available_entities || []);
        setSuggestedClaims(d.suggested_other_claims || null);
        setSuggestedMezz(d.suggested_mezzanine || null);
        const sp = d.liens_headroom?.suggested_priming || null;
        setSuggestedPriming(sp);
        if (sp?.value) setPrimingFace(Math.round(sp.value));
        if (d.base_ebitda) setSim((s) => ({ ...s, base_ebitda: Math.round(d.base_ebitda) }));
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
    listScenarios(ticker).then(setScenarios).catch(() => {});
  }, [ticker, years]);

  const collateral = useMemo(() => {
    if (!structure) return [];
    return structure.entities.map((e) => {
      const sec = structure.tranches.filter((t) => t.entity === e.name && t.secured);
      return {
        pool: `${e.name} all assets`,
        ev_share: e.ev_share,
        secured_face: sec.reduce((a, t) => a + (t.face || 0), 0),
        tranches: sec.map((t) => t.name).join(", ") || "—",
      };
    });
  }, [structure]);

  function patchTranche(i, patch) {
    setStructure((s) => ({
      ...s,
      tranches: s.tranches.map((t, j) => (j === i ? { ...t, ...patch } : t)),
    }));
  }

  function patchEntity(i, patch) {
    setStructure((s) => ({
      ...s,
      entities: s.entities.map((e, j) => (j === i ? { ...e, ...patch } : e)),
    }));
  }

  function addEntityFromExhibit(name) {
    if (!name) return;
    setStructure((s) =>
      s.entities.some((e) => e.name === name)
        ? s
        : { ...s, entities: [...s.entities, { name, ev_share: 0.0, parent: null }] }
    );
  }

  function cleanStructure() {
    return {
      ...structure,
      entities: structure.entities
        .filter((e) => (e.name || "").trim())
        .map((e) => ({ ...e, parent: (e.parent || "").trim() || null })),
      tranches: structure.tranches
        .filter((t) => (t.name || "").trim() && t.face > 0)
        .map((t) => ({ ...t, subordinated_to: (t.subordinated_to || "").trim() || null })),
    };
  }

  async function run(attackKind = attack, primeFace = null) {
    setRunning(true);
    setError(null);
    try {
      const simBody = { ...sim };
      if (simBody.accrual_years == null) delete simBody.accrual_years;  // petition date derives it
      setResult(await simulateRecovery(ticker, cleanStructure(), simBody, years, {
        petition_date: petitionDate || null,
        attack: attackKind || null,
        ...(primeFace > 0 ? { priming: { face: primeFace } } : {}),
      }));
      setAttack(attackKind || null);
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  }

  function addOtherClaimsRow() {
    setStructure((s) => ({
      ...s,
      tranches: [...s.tranches, {
        name: "Other unsecured claims", entity: s.entities[0]?.name || "OpCo",
        face: suggestedClaims?.value || 100, lien_rank: 99, secured: false,
        preferred: false, coupon: 0, make_whole: 0, maturity: "",
      }],
    }));
  }

  function addMezzanineRow() {
    setStructure((s) => ({
      ...s,
      tranches: [...s.tranches, {
        name: "Mezzanine (recast as debt)", entity: s.entities[0]?.name || "OpCo",
        face: suggestedMezz?.value || 100, lien_rank: 99, secured: false,
        preferred: true, coupon: 0, make_whole: 0, maturity: "",
      }],
    }));
  }

  async function onSaveScenario() {
    if (!scenarioName.trim() || !result) return;
    await saveScenario(ticker, {
      name: scenarioName.trim(),
      sim,
      structure,
      results: {
        fulcrum: result.fulcrum,
        ev_median: result.ev.median,
        total_face: result.total_face,
        tranches: result.tranches.map((t) => ({ tranche: t.tranche, mean_recovery_pct: t["mean_recovery_%"] })),
      },
    });
    setScenarioName("");
    setScenarios(await listScenarios(ticker));
  }

  function loadScenario(sc) {
    setStructure(sc.structure);
    setSim(sc.sim);
    setResult(null);
  }

  if (!ticker)
    return (
      <div className="rounded-xl border border-dashed border-ink-700 p-10 text-center text-slate-500">
        Enter a ticker above — the cap table loads from the filed debt schedule.
      </div>
    );
  if (loading)
    return <Loading>Loading capital structure for {ticker}…</Loading>;

  return (
    <div>
      {error && <ErrorCard className="mb-6">{error}</ErrorCard>}

      {structure && (
        <>
          <div className="mb-6 flex flex-wrap items-baseline gap-x-6 gap-y-1 text-sm text-slate-400">
            <span className="text-lg font-semibold text-slate-100">{structure.name}</span>
            <span>cap-table source: <span className="text-slate-200">{source}</span></span>
            <span>{structure.tranches.length} tranches</span>
          </div>

          <Section title="Debt tranches" subtitle="faces in $mm · ranked by lien">
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-xs">
                <thead>
                  <tr className="border-b border-ink-600">
                    <Th>Tranche</Th><Th>Entity</Th><Th right>Face $mm</Th><Th>Secured</Th>
                    <Th right>Lien</Th>
                    <Th right title="caps the secured claim (§506); the shortfall becomes an unsecured deficiency. Empty = all-asset pledge">Collateral $mm</Th>
                    <Th title="contractual subordination: this tranche's recovery redirects to the named tranche until it is paid in full (Moyer ch. 7)">Sub. to</Th>
                    <Th>Preferred</Th><Th right>Coupon %</Th><Th right>Make-whole $mm</Th><Th>Maturity</Th><Th />
                  </tr>
                </thead>
                <tbody>
                  {structure.tranches.map((t, i) => (
                    <tr key={i} className="border-b border-ink-700/60">
                      <td className="min-w-[220px] px-2 py-1">
                        <span className="flex items-center gap-1.5">
                          <TextCell value={t.name} onChange={(v) => patchTranche(i, { name: v })} />
                          <CiteMark citation={citations[t.name]} />
                        </span>
                      </td>
                      <td className="px-2 py-1"><TextCell value={t.entity} onChange={(v) => patchTranche(i, { entity: v })} className="w-24" /></td>
                      <td className="px-2 py-1 text-right"><NumCell value={t.face} step={10} onChange={(v) => patchTranche(i, { face: v ?? 0 })} /></td>
                      <td className="px-2 py-1 text-center">
                        <input type="checkbox" checked={!!t.secured} onChange={(e) => patchTranche(i, { secured: e.target.checked, lien_rank: e.target.checked ? Math.min(t.lien_rank, 3) || 1 : 99 })} className="accent-accent" />
                      </td>
                      <td className="px-2 py-1 text-right"><NumCell value={t.lien_rank} onChange={(v) => patchTranche(i, { lien_rank: v ?? 99 })} className="w-14" /></td>
                      <td className="px-2 py-1 text-right"><NumCell value={t.collateral_value} step={25} onChange={(v) => patchTranche(i, { collateral_value: v })} className="w-20" /></td>
                      <td className="px-2 py-1"><TextCell value={t.subordinated_to} onChange={(v) => patchTranche(i, { subordinated_to: v })} className="w-24" /></td>
                      <td className="px-2 py-1 text-center">
                        <input type="checkbox" checked={!!t.preferred} onChange={(e) => patchTranche(i, { preferred: e.target.checked })} className="accent-accent" />
                      </td>
                      <td className="px-2 py-1 text-right">
                        <NumCell value={t.coupon != null ? +(100 * t.coupon).toFixed(3) : null} step={0.25} onChange={(v) => patchTranche(i, { coupon: (v ?? 0) / 100 })} className="w-16" />
                      </td>
                      <td className="px-2 py-1 text-right"><NumCell value={t.make_whole ?? 0} step={5} onChange={(v) => patchTranche(i, { make_whole: v ?? 0 })} className="w-20" /></td>
                      <td className="px-2 py-1"><TextCell value={t.maturity} onChange={(v) => patchTranche(i, { maturity: v })} className="w-24" /></td>
                      <td className="px-2 py-1">
                        <button onClick={() => setStructure((s) => ({ ...s, tranches: s.tranches.filter((_, j) => j !== i) }))} className="text-slate-500 hover:text-rose-400" title="delete tranche">✕</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Button
                onClick={() => setStructure((s) => ({ ...s, tranches: [...s.tranches, { name: "New tranche", entity: s.entities[0]?.name || "OpCo", face: 100, lien_rank: 99, secured: false, preferred: false, coupon: 0, make_whole: 0, maturity: "" }] }))}
              >
                + Add tranche
              </Button>
              <Button onClick={addOtherClaimsRow}
                title={suggestedClaims?.formula || "rejection damages / pension / lease claims dilute the unsecured pool in chapter 11 (Moyer ch. 12)"}>
                + Other unsecured claims{suggestedClaims?.value ? ` (suggested ${Math.round(suggestedClaims.value).toLocaleString()} $mm)` : ""}
              </Button>
              {suggestedMezz && (
                <Button onClick={addMezzanineRow} title={suggestedMezz.note}>
                  + Add mezzanine (recast as debt) ({Math.round(suggestedMezz.value).toLocaleString()} $mm)
                </Button>
              )}
            </div>
          </Section>

          <div className="grid md:grid-cols-2 md:gap-x-6">
            <Section title="Legal entities" subtitle="EV shares must sum to 1.0 · empty parent = top of the structure">
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-xs">
                  <thead>
                    <tr className="border-b border-ink-600"><Th>Entity</Th><Th right>EV share</Th><Th>Parent</Th><Th /></tr>
                  </thead>
                  <tbody>
                    {structure.entities.map((e, i) => (
                      <tr key={i} className="border-b border-ink-700/60">
                        <td className="px-2 py-1"><TextCell value={e.name} onChange={(v) => patchEntity(i, { name: v })} /></td>
                        <td className="px-2 py-1 text-right"><NumCell value={e.ev_share} step={0.05} onChange={(v) => patchEntity(i, { ev_share: v ?? 0 })} className="w-20" /></td>
                        <td className="px-2 py-1"><TextCell value={e.parent} onChange={(v) => patchEntity(i, { parent: v })} className="w-28" /></td>
                        <td className="px-2 py-1">
                          <button onClick={() => setStructure((s) => ({ ...s, entities: s.entities.filter((_, j) => j !== i) }))} className="text-slate-500 hover:text-rose-400">✕</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="mt-3 flex items-end justify-between gap-3">
                <div className="flex items-center gap-2">
                  <Button
                    onClick={() => setStructure((s) => ({ ...s, entities: [...s.entities, { name: "HoldCo", ev_share: 0.0, parent: null }] }))}
                  >
                    + Add entity
                  </Button>
                  {availableEntities.length > 0 && (
                    <select
                      value=""
                      onChange={(e) => addEntityFromExhibit(e.target.value)}
                      className="max-w-[240px] rounded-md border border-ink-600 bg-ink-800 px-2 py-1.5 text-xs text-slate-300 outline-none focus:border-accent"
                      title="add a legal entity parsed from Exhibit 21"
                    >
                      <option value="">+ Add from Exhibit 21 ({availableEntities.length})…</option>
                      {availableEntities.map((s) => (
                        <option key={s.name} value={s.name}>
                          {s.name}{s.jurisdiction ? ` · ${s.jurisdiction}` : ""}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
                <div className="flex gap-3">
                  <NumField label="Admin / estate fees ($mm)" value={structure.admin_fees} step={5}
                    onChange={(v) => setStructure((s) => ({ ...s, admin_fees: v }))} />
                  <NumField label="Estate costs (% of EV)" value={structure.admin_pct ?? 0} step={0.01}
                    onChange={(v) => setStructure((s) => ({ ...s, admin_pct: v }))} />
                </div>
              </div>
            </Section>

            <Section title="Collateral pools" subtitle="derived — all-asset pledge per entity">
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-xs">
                  <thead>
                    <tr className="border-b border-ink-600"><Th>Pool</Th><Th right>EV share</Th><Th right>Secured face</Th><Th>Secured tranches</Th></tr>
                  </thead>
                  <tbody>
                    {collateral.map((r, i) => (
                      <tr key={i} className="border-b border-ink-700/60 text-slate-300">
                        <td className="px-2 py-1.5">{r.pool}</td>
                        <td className="px-2 py-1.5 text-right font-mono">{fmt(r.ev_share, 2)}</td>
                        <td className="px-2 py-1.5 text-right font-mono">{fmt(r.secured_face)}</td>
                        <td className="px-2 py-1.5 text-slate-400">{r.tranches}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Section>
          </div>

          <Section title="Enterprise-value simulation" subtitle="EV = terminal EBITDA × exit multiple, correlated, with a stress regime">
            <div className="flex flex-wrap gap-x-5 gap-y-4">
              <NumField label="Base EBITDA $mm" value={sim.base_ebitda} step={25} onChange={(v) => setSim({ ...sim, base_ebitda: v })} />
              <NumField label="Horizon yrs" value={sim.horizon_years} step={0.25} onChange={(v) => setSim({ ...sim, horizon_years: v })} />
              <NumField label="EBITDA vol" value={sim.ebitda_vol} step={0.01} onChange={(v) => setSim({ ...sim, ebitda_vol: v })} />
              <NumField label="Mean reversion" value={sim.mean_reversion} step={0.05} onChange={(v) => setSim({ ...sim, mean_reversion: v })} />
              <NumField label="Stress prob" value={sim.stress_prob} step={0.05} onChange={(v) => setSim({ ...sim, stress_prob: v })} />
              <NumField label="Stress vol" value={sim.stress_vol} step={0.01} onChange={(v) => setSim({ ...sim, stress_vol: v })} />
              <NumField label="Stress drift" value={sim.stress_log_drift} step={0.05} onChange={(v) => setSim({ ...sim, stress_log_drift: v })} />
              <NumField label="Multiple (normal)" value={sim.base_multiple} step={0.25} onChange={(v) => setSim({ ...sim, base_multiple: v })} />
              <NumField label="Multiple (stress)" value={sim.distress_multiple} step={0.25} onChange={(v) => setSim({ ...sim, distress_multiple: v })} />
              <NumField label="Multiple vol" value={sim.multiple_vol} step={0.01} onChange={(v) => setSim({ ...sim, multiple_vol: v })} />
              <NumField label="EBITDA×mult corr" value={sim.corr} step={0.05} onChange={(v) => setSim({ ...sim, corr: v })} />
              <Field label="Petition date (tolls accrual)">
                <input type="date" value={petitionDate}
                  onChange={(e) => setPetitionDate(e.target.value)}
                  title="unsecured interest accrues only to the petition date (Moyer); derives the accrual unless set explicitly below"
                  className="w-36 rounded-md border border-ink-600 bg-ink-800 px-2 py-1.5 font-mono text-xs text-slate-100 outline-none focus:border-accent" />
              </Field>
              <Field label="Accrued (yrs, explicit)">
                <NumCell value={sim.accrual_years} step={0.05}
                  onChange={(v) => setSim({ ...sim, accrual_years: v })} className="w-28 py-1.5" />
              </Field>
              <NumField label="Draws" value={sim.n_draws} step={10000} onChange={(v) => setSim({ ...sim, n_draws: v })} />
              <NumField label="Seed" value={sim.seed} step={1} onChange={(v) => setSim({ ...sim, seed: v })} />
              <div className="flex items-end">
                <Button variant="primary" onClick={() => run()} disabled={running}>
                  {running ? "Simulating…" : "Run simulation"}
                </Button>
              </div>
            </div>
            <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
              <span className="text-[10px] uppercase tracking-wide text-slate-500"
                title="never take the stated priority stack as fixed (Moyer ch. 12) — re-runs the waterfall on the same EV draws">
                Priority attacks:
              </span>
              {[["lien_avoidance", "lien avoidance"], ["equitable_subordination", "equitable subordination"],
                ["substantive_consolidation", "substantive consolidation"]].map(([k, label]) => (
                <Button key={k} onClick={() => run(attack === k ? null : k)} disabled={running}
                  className={attack === k ? "border-rose-500/60 text-rose-200" : ""}>
                  {label}{attack === k ? " ✕" : ""}
                </Button>
              ))}
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
              <span className="text-[10px] uppercase tracking-wide text-slate-500"
                title="a new rank-0 secured layer under permitted-lien capacity (or a covenant-lite gap) — re-runs the waterfall on the same EV draws (Moyer ch. 9)">
                Priming scenario:
              </span>
              <NumCell value={primingFace} step={100} onChange={setPrimingFace} className="w-28" />
              <span className="text-slate-500">$mm</span>
              <Button onClick={() => run(attack, primingFace)} disabled={running || !(primingFace > 0)}>
                Run priming
              </Button>
              {suggestedPriming && (
                <span className="text-slate-500" title={suggestedPriming.note}>
                  pre-seeded from liens headroom — {suggestedPriming.basis}
                </span>
              )}
            </div>
          </Section>

          <EvExplorer ticker={ticker} years={years} structure={structure}
            baseEbitda={sim.base_ebitda} accrualYears={sim.accrual_years ?? 0} />

          <ExchangeAnalyzer ticker={ticker} years={years} structure={structure}
            baseEbitda={sim.base_ebitda} accrualYears={sim.accrual_years ?? 0} />
        </>
      )}

      {result?.mode === "liquidation" && (
        <>
          <div className="mb-4 rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">
            {result.note}
          </div>
          <LiquidationPanel ticker={ticker} years={years} structure={structure} initial={result} />
        </>
      )}

      {result && !result.mode && (
        <Results result={result} citations={citations} quotedByName={quotedByName} />
      )}

      {result && !result.mode && (
        <Section title="Scenarios" subtitle="save this run, compare side-by-side">
          <div className="mb-4 flex gap-2">
            <Input value={scenarioName} onChange={(e) => setScenarioName(e.target.value)}
              placeholder="e.g. Base / Bear / Priming" className="w-56" />
            <Button onClick={onSaveScenario} disabled={!scenarioName.trim()}>
              Save scenario
            </Button>
          </div>
          {scenarios.length > 0 && (
            <table className="w-full border-collapse text-xs">
              <thead>
                <tr className="border-b border-ink-600">
                  <Th>Name</Th><Th>Fulcrum</Th><Th right>EV median</Th><Th right>Total face</Th>
                  <Th right>Mean recovery by tranche</Th><Th>Saved</Th><Th />
                </tr>
              </thead>
              <tbody>
                {scenarios.map((sc) => (
                  <tr key={sc.id} className="border-b border-ink-700/60 text-slate-300">
                    <td className="px-2 py-1.5 font-semibold text-slate-100">{sc.name}</td>
                    <td className="px-2 py-1.5">{sc.results?.fulcrum || "—"}</td>
                    <td className="px-2 py-1.5 text-right font-mono">{fmt(sc.results?.ev_median, 0)}</td>
                    <td className="px-2 py-1.5 text-right font-mono">{fmt(sc.results?.total_face, 0)}</td>
                    <td className="px-2 py-1.5 text-right font-mono text-slate-400">
                      {(sc.results?.tranches || []).map((t) => `${t.tranche.slice(0, 14)} ${fmt(t.mean_recovery_pct, 0)}%`).join(" · ")}
                    </td>
                    <td className="px-2 py-1.5 text-slate-500">{(sc.created_at || "").slice(0, 10)}</td>
                    <td className="px-2 py-1.5 whitespace-nowrap">
                      <button onClick={() => loadScenario(sc)} className="mr-2 text-accent hover:underline">load</button>
                      <button onClick={async () => { await deleteScenario(sc.id); setScenarios(await listScenarios(ticker)); }}
                        className="text-slate-500 hover:text-rose-400">✕</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      )}
    </div>
  );
}

function Results({ result, citations = {}, quotedByName = {} }) {
  const order = result.tranches.map((t) => t.tranche);
  const histTranches = order.slice(0, 8);
  const cdfData = result.cdf.grid.map((g, i) => {
    const row = { pct: g };
    for (const name of histTranches) row[name] = 100 * result.cdf.series[name][i];
    return row;
  });
  const evHist = result.ev.histogram.counts.map((c, i) => ({
    ev: Math.round((result.ev.histogram.edges[i] + result.ev.histogram.edges[i + 1]) / 2),
    n: c,
  }));

  return (
    <>
      <div className={`mb-6 rounded-xl border p-4 text-sm ${result.fulcrum ? "border-rose-500/50 bg-rose-500/10 text-rose-100" : "border-emerald-500/40 bg-emerald-500/10 text-emerald-100"}`}>
        {result.fulcrum ? (
          <>
            <span className="font-bold">Fulcrum: {result.fulcrum}</span> — first impaired class at median EV.
          </>
        ) : (
          "No fulcrum — all classes covered at median EV."
        )}
      </div>

      {Object.keys(result.headroom_506 || {}).length > 0 && (
        <div className="mb-6 text-xs text-slate-400">
          <span className="text-[10px] uppercase tracking-wide text-slate-500"
            title="postpetition interest accrues only to the extent collateral value exceeds the claim (§506)">
            §506 postpetition-interest headroom:
          </span>{" "}
          {Object.entries(result.headroom_506).map(([n, v]) => (
            <span key={n} className="mr-4">{n}: <span className="font-mono text-slate-200">{fmt(v, 0)} $mm</span></span>
          ))}
        </div>
      )}

      {result.attack_tranches && (
        <Section title={`Priority attack: ${result.attack.replace(/_/g, " ")}`}
          subtitle="same EV draws, transformed structure — mean recovery vs base (Moyer ch. 12)">
          <table className="w-full max-w-xl border-collapse text-xs">
            <thead>
              <tr className="border-b border-ink-600">
                <Th>Tranche</Th><Th right>Base mean %</Th><Th right>Attacked mean %</Th><Th right>Δ</Th>
              </tr>
            </thead>
            <tbody>
              {result.attack_tranches.map((a) => {
                const base = result.tranches.find((t) => t.tranche === a.tranche);
                const delta = base && a["mean_recovery_%"] != null
                  ? a["mean_recovery_%"] - base["mean_recovery_%"] : null;
                return (
                  <tr key={a.tranche} className="border-b border-ink-700/60 font-mono text-slate-300">
                    <td className="px-2 py-1.5 font-sans">{a.tranche}</td>
                    <td className="px-2 py-1.5 text-right">{base ? fmt(base["mean_recovery_%"]) : "—"}</td>
                    <td className="px-2 py-1.5 text-right">{fmt(a["mean_recovery_%"])}</td>
                    <td className={`px-2 py-1.5 text-right font-semibold ${delta > 0 ? "text-emerald-300" : delta < 0 ? "text-rose-300" : "text-slate-500"}`}>
                      {delta == null ? "—" : `${delta > 0 ? "+" : ""}${fmt(delta)}`}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Section>
      )}

      {result.priming_tranches && (
        <Section title="Priming scenario"
          subtitle="same EV draws, new rank-0 secured layer ahead of every lien — mean recovery vs base (Moyer ch. 9)">
          <table className="w-full max-w-xl border-collapse text-xs">
            <thead>
              <tr className="border-b border-ink-600">
                <Th>Tranche</Th><Th right>Base mean %</Th><Th right>Primed mean %</Th><Th right>Δ</Th>
              </tr>
            </thead>
            <tbody>
              {result.priming_tranches.map((p) => {
                const base = result.tranches.find((t) => t.tranche === p.tranche);
                const delta = base && p["mean_recovery_%"] != null
                  ? p["mean_recovery_%"] - base["mean_recovery_%"] : null;
                const spec = (result.primed_structure?.tranches || [])
                  .find((t) => t.name === p.tranche);
                const unsecured = spec && !spec.secured && !spec.preferred;
                return (
                  <tr key={p.tranche}
                    className={`border-b border-ink-700/60 font-mono ${unsecured ? "bg-rose-900/20 text-rose-200" : "text-slate-300"}`}>
                    <td className="px-2 py-1.5 font-sans">{p.tranche}</td>
                    <td className="px-2 py-1.5 text-right">{base ? fmt(base["mean_recovery_%"]) : "—"}</td>
                    <td className="px-2 py-1.5 text-right">{fmt(p["mean_recovery_%"])}</td>
                    <td className={`px-2 py-1.5 text-right font-semibold ${delta > 0 ? "text-emerald-300" : delta < 0 ? "text-rose-300" : "text-slate-500"}`}>
                      {delta == null ? "—" : `${delta > 0 ? "+" : ""}${fmt(delta)}`}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="mt-1 text-[11px] text-slate-500">
            unsecured rows highlighted — priming shifts their value up the stack. To explore
            the primed structure at any EV, add the priming face as a lien-0 secured tranche
            in the editor above (the EV explorer runs on the edited structure).
          </div>
        </Section>
      )}

      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
        {[
          ["EV median $mm", fmt(result.ev.median, 0)],
          ["EV P10 / P90", `${fmt(result.ev.p10, 0)} / ${fmt(result.ev.p90, 0)}`],
          ["Total face $mm", fmt(result.total_face, 0)],
          ["Fulcrum", result.fulcrum || "none"],
        ].map(([label, value]) => (
          <div key={label} className="rounded-xl border border-ink-700 bg-ink-800/50 p-3">
            <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
            <div className="mt-1 font-mono text-lg text-slate-100">{value}</div>
          </div>
        ))}
      </div>

      <Section title="Recovery by tranche" subtitle="most-senior first · % of allowed claim (principal + accrued + make-whole) · fulcrum highlighted">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="border-b border-ink-600">
                <Th>Tranche</Th><Th>Entity</Th><Th right>Face</Th><Th right>Claim</Th><Th right>Mean %</Th><Th right>Mean $</Th>
                <Th right>Median %</Th><Th right>P10 %</Th><Th right>P90 %</Th><Th right>LGD %</Th>
                <Th right>P(impaired)</Th><Th right>P(zero)</Th>
                <Th right title="33.4% of class face blocks plan acceptance (66.7%-in-amount vote test) · cost at the drop-file quote when matched">Block 33.4%</Th>
              </tr>
            </thead>
            <tbody>
              {result.tranches.map((t) => (
                <tr key={t.tranche} className={`border-b border-ink-700/60 font-mono ${t.is_fulcrum ? "bg-rose-900/40 text-rose-100" : "text-slate-300"}`}>
                  <td className="px-2 py-1.5 font-sans">
                    {t.tranche} <CiteMark citation={citations[t.tranche]} />
                    {t.is_fulcrum && <span className="ml-2 rounded bg-rose-500/30 px-1.5 py-0.5 text-[9px] uppercase">fulcrum</span>}
                  </td>
                  <td className="px-2 py-1.5 font-sans text-slate-400">{t.entity}</td>
                  <td className="px-2 py-1.5 text-right">{fmt(t.face, 0)}</td>
                  <td className="px-2 py-1.5 text-right" title={t["accrued_$"] || t["make_whole_$"] ? `+ accrued ${fmt(t["accrued_$"], 0)} + make-whole ${fmt(t["make_whole_$"], 0)}` : ""}>{fmt(t.claim, 0)}</td>
                  <td className="px-2 py-1.5 text-right">{fmt(t["mean_recovery_%"])}</td>
                  <td className="px-2 py-1.5 text-right">{fmt(t["mean_recovery_$"], 0)}</td>
                  <td className="px-2 py-1.5 text-right">{fmt(t["median_recovery_%"])}</td>
                  <td className="px-2 py-1.5 text-right">{fmt(t["p10_%"])}</td>
                  <td className="px-2 py-1.5 text-right">{fmt(t["p90_%"])}</td>
                  <td className="px-2 py-1.5 text-right">{fmt(t["lgd_%"])}</td>
                  <td className="px-2 py-1.5 text-right">{fmt(t["prob_impaired_%"])}</td>
                  <td className="px-2 py-1.5 text-right">{fmt(t["prob_zero_%"])}</td>
                  <td className="px-2 py-1.5 text-right">
                    {fmt(0.334 * t.face, 0)}
                    {quotedByName[t.tranche]?.last_price != null && (
                      <span className="text-slate-500" title={`at quote ${quotedByName[t.tranche].last_price}`}>
                        {" "}(${fmt(0.334 * t.face * quotedByName[t.tranche].last_price / 100, 0)})
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section title="Enterprise-value distribution" subtitle={`${fmt(result.sim?.n_draws ?? 0, 0)} draws`}>
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={evHist} margin={{ top: 4, right: 8, bottom: 4, left: 8 }}>
            <CartesianGrid stroke={INK[600]} strokeDasharray="3 3" />
            <XAxis dataKey="ev" tick={{ fill: "#94a3b8", fontSize: 10 }} />
            <YAxis tick={{ fill: "#94a3b8", fontSize: 10 }} />
            <Tooltip contentStyle={chartTooltipStyle} labelFormatter={(v) => `EV ≈ ${fmt(v, 0)} $mm`} />
            <Bar dataKey="n" fill={ACCENT} />
          </BarChart>
        </ResponsiveContainer>
      </Section>

      <Section title="Recovery distributions" subtitle="% of allowed claim · per tranche">
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {histTranches.map((name, idx) => {
            const h = result.histograms[name];
            const data = h.counts.map((c, i) => ({ pct: Math.round((h.edges[i] + h.edges[i + 1]) / 2), n: c }));
            const isFulcrum = name === result.fulcrum;
            return (
              <div key={name}>
                <div className={`mb-1 truncate text-xs ${isFulcrum ? "font-semibold text-rose-300" : "text-slate-400"}`} title={name}>
                  {name}{isFulcrum ? " ← fulcrum" : ""}
                </div>
                <ResponsiveContainer width="100%" height={110}>
                  <BarChart data={data} margin={{ top: 0, right: 0, bottom: 0, left: -18 }}>
                    <XAxis dataKey="pct" tick={{ fill: "#64748b", fontSize: 9 }} />
                    <YAxis tick={{ fill: "#64748b", fontSize: 9 }} />
                    <Tooltip contentStyle={chartTooltipStyle} labelFormatter={(v) => `${v}% of claim`} />
                    <Bar dataKey="n" fill={isFulcrum ? RISK.high : LINE_COLORS[idx % LINE_COLORS.length]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            );
          })}
        </div>
      </Section>

      <Section title="Recovery CDF" subtitle="P(recovery ≤ x% of allowed claim)">
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={cdfData} margin={{ top: 4, right: 8, bottom: 4, left: 8 }}>
            <CartesianGrid stroke={INK[600]} strokeDasharray="3 3" />
            <XAxis dataKey="pct" tick={{ fill: "#94a3b8", fontSize: 10 }} unit="%" />
            <YAxis tick={{ fill: "#94a3b8", fontSize: 10 }} unit="%" />
            <Tooltip contentStyle={chartTooltipStyle} formatter={(v) => `${fmt(v, 1)}%`} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {histTranches.map((name, i) => (
              <Line key={name} dataKey={name} stroke={LINE_COLORS[i % LINE_COLORS.length]} dot={false} strokeWidth={name === result.fulcrum ? 2.5 : 1.5} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </Section>

      <IrrMatrix tranches={result.tranches} quotedByName={quotedByName} />

      <Section title="Waterfall at median EV" subtitle="single-path allocation at the median draw · recovery % of allowed claim">
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr className="border-b border-ink-600"><Th>Tranche</Th><Th right>Face $mm</Th><Th right>Claim $mm</Th><Th right>Recovery $mm</Th><Th right>Recovery %</Th></tr>
          </thead>
          <tbody>
            {result.waterfall_at_median.map((r) => (
              <tr key={r.tranche} className="border-b border-ink-700/60 font-mono text-slate-300">
                <td className="px-2 py-1.5 font-sans">{r.tranche}</td>
                <td className="px-2 py-1.5 text-right">{fmt(r.face, 0)}</td>
                <td className="px-2 py-1.5 text-right">{fmt(r.claim, 0)}</td>
                <td className="px-2 py-1.5 text-right">{fmt(r.recovery, 0)}</td>
                <td className="px-2 py-1.5 text-right">{r.recovery_pct == null ? "—" : fmt(r.recovery_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
    </>
  );
}
