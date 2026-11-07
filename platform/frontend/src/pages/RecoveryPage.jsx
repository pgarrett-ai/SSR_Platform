import React, { useEffect, useState } from "react";
import CitedNumber from "../components/CitedNumber.jsx";
import {
  Badge, Button, ErrorCard, Field, Loading, NumCell, NumField, Section, Td, TextCell, Th,
  fmt, fmtMoney, fmtX,
} from "../ui/index.jsx";
import { fetchLadder, fetchRecoveryStructure, simulateRecovery } from "../api.js";
import CaseCard from "../components/CaseCard.jsx";
import EvExplorer from "../components/EvExplorer.jsx";
import ExchangeAnalyzer from "../components/ExchangeAnalyzer.jsx";
import IrrMatrix from "../components/IrrMatrix.jsx";
import LiquidationPanel from "../components/LiquidationPanel.jsx";

// The recovery engine works in $M; the kit formats raw USD.
const mm = (v) => fmtMoney(v == null ? v : v * 1e6);

// § beside a tranche opens the filing citation behind its face amount.
function CiteMark({ citation }) {
  if (!citation) return null;
  return <CitedNumber cv={{ display: "§", citation }} className="text-accent" />;
}

// The seed is fixed so the strip, this page and every rehearsal show the same numbers.
// base_ebitda null = not extracted: the server then values the estate in liquidation.
const SIM_DEFAULTS = {
  base_ebitda: null, horizon_years: 1.5, ebitda_vol: 0.28, mean_reversion: 0.6,
  stress_prob: 0.3, stress_vol: 0.55, stress_log_drift: -0.35, base_multiple: 6.0,
  distress_multiple: 4.5, multiple_vol: 0.18, corr: 0.55,
  accrual_years: null,   // null = derived from the petition date; a number overrides
  n_draws: 50000, seed: 42,
};

const ATTACKS = [
  ["lien_avoidance", "Lien avoidance", "secured claims lose their liens and share as unsecured"],
  ["equitable_subordination", "Equitable subordination",
    "secured claims drop below every unsecured claim, ahead of equity only"],
  ["substantive_consolidation", "Substantive consolidation",
    "the legal entities merge, so structural subordination disappears"],
];

const dropNulls = (o) => Object.fromEntries(Object.entries(o).filter(([, v]) => v != null));

function cleanStructure(s) {
  return {
    ...s,
    entities: s.entities
      .filter((e) => (e.name || "").trim())
      .map((e) => ({ ...e, parent: (e.parent || "").trim() || null })),
    tranches: s.tranches
      .filter((t) => (t.name || "").trim() && t.face > 0)
      .map((t) => ({ ...t, subordinated_to: (t.subordinated_to || "").trim() || null })),
  };
}

// Where the fulcrum is, what each class recovers and the return at today's price. The
// simulation runs as soon as the cap table loads; every section below reads that one run.
export default function RecoveryPage({ ticker, years, onRecoveryResult }) {
  const [ctx, setCtx] = useState(null);             // the /recovery/structure response
  const [structure, setStructure] = useState(null); // editable cap table
  const [sim, setSim] = useState(SIM_DEFAULTS);
  const [result, setResult] = useState(null);       // the base run
  const [stress, setStress] = useState(null);       // {key, label, base, rows}
  const [primingFace, setPrimingFace] = useState(null);
  const [running, setRunning] = useState(null);     // "base" | "stress"
  const [error, setError] = useState(null);
  const [quotes, setQuotes] = useState({});         // instrument -> last bond quote

  const petitionDate = ctx?.chapter11?.petition_date || null;

  useEffect(() => {
    fetchLadder(ticker, years)
      .then((d) => setQuotes(d.quote_by_instrument || {}))
      .catch(() => setQuotes({}));
  }, [ticker, years]);

  useEffect(() => {
    let alive = true;   // a superseded mount never starts its own run
    fetchRecoveryStructure(ticker, years)
      .then((d) => {
        if (!alive) return;
        // estate costs default to 7% of EV (the engine's default is 0): professional
        // fees run higher than outsiders assume
        const st = { ...d.structure, admin_pct: d.structure.admin_pct || 0.07 };
        const sm = { ...SIM_DEFAULTS, base_ebitda: d.base_ebitda == null ? null : Math.round(d.base_ebitda) };
        setCtx(d);
        setStructure(st);
        setSim(sm);
        const sp = d.liens_headroom?.suggested_priming?.value;
        if (sp) setPrimingFace(Math.round(sp));
        run(st, sm, d.chapter11?.petition_date);
      })
      .catch((e) => alive && setError(e.message));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker, years]);

  async function run(st = structure, sm = sim, petition = petitionDate) {
    setRunning("base");
    setError(null);
    try {
      const r = await simulateRecovery(ticker, cleanStructure(st), dropNulls(sm), years,
        { petition_date: petition || null });
      setResult(r);
      setStress(null);
      onRecoveryResult?.(r.recovery_summary ?? null);
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(null);
    }
  }

  // Same stack, sim and EV draws as the base run, transformed; the base table stays put.
  async function runStress(key, label, extra) {
    if (stress?.key === key && key !== "priming") {
      setStress(null);
      return;
    }
    setRunning("stress");
    setError(null);
    try {
      const r = await simulateRecovery(ticker, result.structure, result.sim, years, extra);
      setStress({ key, label, base: r.tranches, rows: r.attack_tranches || r.priming_tranches || [] });
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(null);
    }
  }

  if (error && !structure) return <ErrorCard>{error}</ErrorCard>;
  if (!structure) return <Loading>Loading the capital structure for {ticker}…</Loading>;

  const liquidation = result?.mode === "liquidation";
  const going = result && !liquidation;

  return (
    <div>
      {error && <ErrorCard className="mb-6">{error}</ErrorCard>}
      {petitionDate && <CaseCard ch11={ctx.chapter11} />}

      {!result && running && <Loading>Simulating recoveries…</Loading>}
      {liquidation && <LiquidationPanel ticker={ticker} years={years} initial={result} />}

      {going && (
        <>
          <AnswerLine result={result} />
          <TrancheTable result={result} citations={ctx.citations || {}} quotes={quotes} />
          <IrrMatrix tranches={result.tranches} quotedByName={quotes} />
          <EvExplorer ticker={ticker} years={years} result={result} />
          <Section title="Stress the stack"
            subtitle="the same EV draws through a transformed stack · mean recovery vs base">
            <div className="flex flex-wrap items-center gap-2">
              {ATTACKS.filter(([k]) => k !== "substantive_consolidation"
                || result.structure.entities.length > 1).map(([k, label, tip]) => (
                <Button key={k} title={tip} disabled={!!running}
                  onClick={() => runStress(k, label, { attack: k })}
                  className={stress?.key === k ? "border-accent text-white" : ""}>
                  {label}
                </Button>
              ))}
              <span className="ml-3 text-[11px] uppercase tracking-wide text-slate-400">Priming $M</span>
              <NumCell value={primingFace} step={100} onChange={setPrimingFace} className="w-24" />
              <Button disabled={!!running || !(primingFace > 0)}
                title={ctx.liens_headroom?.suggested_priming?.basis
                  ? `pre-seeded from the liens covenant: ${ctx.liens_headroom.suggested_priming.basis}`
                  : "a new secured layer ranked ahead of every existing lien"}
                onClick={() => runStress("priming", `Primed by ${mm(primingFace)}`,
                  { priming: { face: primingFace } })}
                className={stress?.key === "priming" ? "border-accent text-white" : ""}>
                Prime
              </Button>
            </div>
            {running === "stress" && <Loading className="py-4">Re-running the waterfall…</Loading>}
            {stress && running !== "stress" && <DeltaTable stress={stress} />}
          </Section>
        </>
      )}

      {result && (
        <ExchangeAnalyzer ticker={ticker} years={years} structure={result.structure}
          baseEbitda={result.sim?.base_ebitda ?? sim.base_ebitda}
          accrualYears={result.accrual_years ?? 0} />
      )}

      <Assumptions
        ctx={ctx} structure={structure} setStructure={setStructure} sim={sim} setSim={setSim}
        accrualUsed={result?.accrual_years} running={running} onRun={() => run()} />
    </div>
  );
}

// One line: what the estate is worth against what is owed. The fulcrum lives in the strip
// and the highlighted rows below.
function AnswerLine({ result }) {
  const { ev, sim } = result;
  const evCv = (v, stat) => ({
    value: v, display: mm(v), derived: true,
    formula: `${stat} of ${fmt(sim.n_draws, 0)} draws of terminal EBITDA (from ${mm(sim.base_ebitda)}) `
      + `× exit multiple (${fmtX(sim.base_multiple)} normal, ${fmtX(sim.distress_multiple)} stress); `
      + `estate costs (${Math.round(100 * (result.structure.admin_pct || 0))}%) come off before any class is paid`,
  });
  const claims = {
    value: result.total_claim, display: mm(result.total_claim), derived: true,
    formula: `principal ${mm(result.total_face)} + accrued interest + make-whole, `
      + `${result.tranches.length} tranches`,
  };
  return (
    <p className="mb-6 text-base text-slate-300">
      EV median <CitedNumber cv={evCv(ev.median, "median")} className="text-lg text-slate-100" />{" "}
      <span className="text-sm text-slate-400">
        (P10 <CitedNumber cv={evCv(ev.p10, "10th percentile")} /> – P90{" "}
        <CitedNumber cv={evCv(ev.p90, "90th percentile")} />)
      </span>{" "}
      vs allowed claims <CitedNumber cv={claims} className="text-lg text-slate-100" />
    </p>
  );
}

function TrancheTable({ result, citations, quotes }) {
  const atMedian = Object.fromEntries((result.waterfall_at_median || []).map((r) => [r.tranche, r.recovery]));
  const firstFulcrum = result.tranches.find((t) => t.is_fulcrum)?.tranche;
  const pct = (v) => fmt(v, 1);
  return (
    <Section title="Recovery by tranche"
      subtitle="most senior first · % of allowed claim (principal + accrued + make-whole) · fulcrum class highlighted">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-ink-600">
              <Th>Tranche</Th>
              <Th right>Face $M</Th>
              <Th right title="principal + accrued interest to the petition + make-whole">Claim $M</Th>
              <Th right>Mean %</Th>
              <Th right>P10 %</Th>
              <Th right>P90 %</Th>
              <Th right title="share of draws in which the class recovers less than its full claim">P(impaired) %</Th>
              <Th right title="share of draws in which the class recovers nothing">P(zero) %</Th>
              <Th right title="dollars the class recovers at the median EV draw">At median EV $M</Th>
              <Th right title="33.4% of this tranche's face at the last matched bond quote. A blocking position needs one third of the whole class's allowed claims (the two-thirds-in-amount vote), so for a multi-tranche class it costs more">
                Block 33.4% $M
              </Th>
            </tr>
          </thead>
          <tbody>
            {result.tranches.map((t) => {
              const q = quotes[t.tranche]?.last_price;
              return (
                <tr key={t.tranche}
                  className={`border-b border-ink-700/60 ${t.is_fulcrum ? "bg-rose-500/10" : ""}`}>
                  <Td className={t.is_fulcrum ? "text-rose-100" : "text-slate-200"}>
                    {t.tranche} <CiteMark citation={citations[t.tranche]} />
                    {t.tranche === firstFulcrum && <Badge tone="high" className="ml-2">fulcrum</Badge>}
                  </Td>
                  <Td right mono className="text-slate-300">{fmt(t.face, 0)}</Td>
                  <Td right mono className="text-slate-300"
                    title={t["accrued_$"] || t["make_whole_$"]
                      ? `+ accrued ${fmt(t["accrued_$"], 0)} + make-whole ${fmt(t["make_whole_$"], 0)}` : undefined}>
                    {fmt(t.claim, 0)}
                  </Td>
                  <Td right mono className="text-slate-100">{pct(t["mean_recovery_%"])}</Td>
                  <Td right mono className="text-slate-400">{pct(t["p10_%"])}</Td>
                  <Td right mono className="text-slate-400">{pct(t["p90_%"])}</Td>
                  <Td right mono className="text-slate-400">{pct(t["prob_impaired_%"])}</Td>
                  <Td right mono className="text-slate-400">{pct(t["prob_zero_%"])}</Td>
                  <Td right mono className="text-slate-300">{fmt(atMedian[t.tranche], 0)}</Td>
                  <Td right mono className="text-slate-400">
                    {fmt(0.334 * t.face, 0)}
                    {q != null && (
                      <span className="text-slate-500" title={`cost at the last quote of ${q}`}>
                        {" "}({fmt((0.334 * t.face * q) / 100, 0)})
                      </span>
                    )}
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

// Base vs stressed mean recovery for whichever attack or priming run is active, in the base
// table's order (a transformed stack re-sorts by its new priority); a new layer leads.
function DeltaTable({ stress }) {
  const base = Object.fromEntries(stress.base.map((t) => [t.tranche, t["mean_recovery_%"]]));
  const at = new Map(stress.base.map((t, i) => [t.tranche, i]));
  const rows = [...stress.rows].sort((a, b) => (at.get(a.tranche) ?? -1) - (at.get(b.tranche) ?? -1));
  return (
    <div className="mt-4 overflow-x-auto">
      <table className="w-full max-w-2xl text-sm">
        <thead>
          <tr className="border-b border-ink-600">
            <Th>Tranche</Th>
            <Th right>Base mean %</Th>
            <Th right>{stress.label} %</Th>
            <Th right title="percentage points of allowed claim">Δ pp</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const b = base[r.tranche];
            const s = r["mean_recovery_%"];
            const d = b != null && s != null ? s - b : null;
            return (
              <tr key={r.tranche} className="border-b border-ink-700/60">
                <Td className="text-slate-200">{r.tranche}</Td>
                <Td right mono className="text-slate-400">{fmt(b, 1)}</Td>
                <Td right mono className="text-slate-100">{fmt(s, 1)}</Td>
                <Td right mono className={`font-semibold ${
                  d > 0.05 ? "text-emerald-300" : d < -0.05 ? "text-rose-300" : "text-slate-500"}`}>
                  {d == null ? "—" : `${d > 0 ? "+" : ""}${fmt(d, 1)}`}
                </Td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// Every input the answer rests on, closed by default; the summary line names them (and the
// model's limits) while it stays closed.
function Assumptions({ ctx, structure, setStructure, sim, setSim, accrualUsed, running, onRun }) {
  const citations = ctx?.citations || {};
  const available = ctx?.available_entities || [];
  const claims = ctx?.suggested_other_claims;
  const mezz = ctx?.suggested_mezzanine;
  const label = (ctx?.base_ebitda_label || "").replace(/\.$/, "");
  const n = structure.entities.length;
  const costs = `${Math.round(100 * (structure.admin_pct || 0))}%${structure.admin_fees ? ` + ${mm(structure.admin_fees)}` : ""}`;
  const summary = [
    `base EBITDA ${sim.base_ebitda == null ? "not extracted" : mm(sim.base_ebitda)}${label ? ` (${label})` : ""}`,
    `normal/stress multiple ${fmtX(sim.base_multiple)}/${fmtX(sim.distress_multiple)}`,
    `estate costs ${costs}`,
    `accrual ${fmt(accrualUsed ?? sim.accrual_years ?? 0, 2)} yrs`,
    `${n === 1 ? "single legal entity" : `${n} legal entities`}, guarantees not modeled`,
  ].join(" · ");

  const patchTranche = (i, patch) => setStructure((s) => ({
    ...s, tranches: s.tranches.map((t, j) => (j === i ? { ...t, ...patch } : t)) }));
  const patchEntity = (i, patch) => setStructure((s) => ({
    ...s, entities: s.entities.map((e, j) => (j === i ? { ...e, ...patch } : e)) }));
  const addTranche = (row) => setStructure((s) => ({
    ...s,
    tranches: [...s.tranches, {
      entity: s.entities[0]?.name || "OpCo", lien_rank: 99, secured: false, preferred: false,
      coupon: 0, make_whole: 0, maturity: "", ...row,
    }],
  }));
  const addEntity = (name) => name && setStructure((s) => (s.entities.some((e) => e.name === name)
    ? s : { ...s, entities: [...s.entities, { name, ev_share: 0.0, parent: null }] }));
  const knob = (key, lbl, step) => (
    <NumField key={key} label={lbl} value={sim[key]} step={step}
      onChange={(v) => setSim((s) => ({ ...s, [key]: v }))} />
  );

  return (
    <Section collapsible defaultOpen={false} title="Assumptions" subtitle={summary}>
      <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
        Tranches · $M · ranked by lien
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr className="border-b border-ink-600">
              <Th>Tranche</Th><Th>Entity</Th><Th right>Face</Th><Th>Secured</Th><Th right>Lien</Th>
              <Th right title="caps the secured claim; the shortfall joins the unsecured pool. Empty = all-asset pledge">Collateral</Th>
              <Th title="contractual subordination: this tranche's recovery goes to the named tranche until it is paid in full">Sub. to</Th>
              <Th>Preferred</Th><Th right>Coupon %</Th><Th right>Make-whole</Th><Th>Maturity</Th><Th />
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
                  <input type="checkbox" checked={!!t.secured} aria-label={`${t.name} secured`}
                    onChange={(e) => patchTranche(i, { secured: e.target.checked, lien_rank: e.target.checked ? Math.min(t.lien_rank, 3) || 1 : 99 })}
                    className="accent-accent" />
                </td>
                <td className="px-2 py-1 text-right"><NumCell value={t.lien_rank} onChange={(v) => patchTranche(i, { lien_rank: v ?? 99 })} className="w-14" /></td>
                <td className="px-2 py-1 text-right"><NumCell value={t.collateral_value} step={25} onChange={(v) => patchTranche(i, { collateral_value: v })} className="w-20" /></td>
                <td className="px-2 py-1"><TextCell value={t.subordinated_to} onChange={(v) => patchTranche(i, { subordinated_to: v })} className="w-24" /></td>
                <td className="px-2 py-1 text-center">
                  <input type="checkbox" checked={!!t.preferred} aria-label={`${t.name} preferred`}
                    onChange={(e) => patchTranche(i, { preferred: e.target.checked })} className="accent-accent" />
                </td>
                <td className="px-2 py-1 text-right">
                  <NumCell value={t.coupon != null ? +(100 * t.coupon).toFixed(3) : null} step={0.25}
                    onChange={(v) => patchTranche(i, { coupon: (v ?? 0) / 100 })} className="w-16" />
                </td>
                <td className="px-2 py-1 text-right"><NumCell value={t.make_whole ?? 0} step={5} onChange={(v) => patchTranche(i, { make_whole: v ?? 0 })} className="w-20" /></td>
                <td className="px-2 py-1"><TextCell value={t.maturity} onChange={(v) => patchTranche(i, { maturity: v })} className="w-24" /></td>
                <td className="px-2 py-1">
                  <button onClick={() => setStructure((s) => ({ ...s, tranches: s.tranches.filter((_, j) => j !== i) }))}
                    className="text-slate-500 hover:text-rose-400" aria-label={`delete ${t.name}`}>✕</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button onClick={() => addTranche({ name: "New tranche", face: 100 })}>+ Add tranche</Button>
        <Button title={claims?.formula || "rejection damages, pension and lease claims dilute the unsecured pool"}
          onClick={() => addTranche({ name: "Other unsecured claims", face: claims?.value || 100 })}>
          + Other unsecured claims{claims?.value ? ` (suggested ${mm(claims.value)})` : ""}
        </Button>
        {mezz && (
          <Button title={mezz.note}
            onClick={() => addTranche({ name: "Mezzanine (recast as debt)", face: mezz.value, preferred: true })}>
            + Mezzanine recast as debt ({mm(mezz.value)})
          </Button>
        )}
      </div>

      <h3 className="mb-2 mt-6 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
        Legal entities · EV shares sum to 1.0 · empty parent = top of the structure
      </h3>
      <div className="flex flex-wrap items-start gap-x-8 gap-y-4">
        <div className="overflow-x-auto">
          <table className="border-collapse text-xs">
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
                    <button onClick={() => setStructure((s) => ({ ...s, entities: s.entities.filter((_, j) => j !== i) }))}
                      className="text-slate-500 hover:text-rose-400" aria-label={`delete ${e.name}`}>✕</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button onClick={() => addEntity(n ? "HoldCo" : "OpCo")}>+ Add entity</Button>
            {available.length > 0 && (
              <select value="" onChange={(e) => addEntity(e.target.value)} aria-label="Add a legal entity from Exhibit 21"
                className="max-w-[240px] rounded-md border border-ink-600 bg-ink-800 px-2 py-1.5 text-xs text-slate-300 outline-none focus:border-accent">
                <option value="">+ Add from Exhibit 21 ({available.length})…</option>
                {available.map((s) => (
                  <option key={s.name} value={s.name}>{s.name}{s.jurisdiction ? ` · ${s.jurisdiction}` : ""}</option>
                ))}
              </select>
            )}
          </div>
        </div>
        <div className="flex flex-wrap gap-3">
          <NumField label="Estate costs (% of EV)" value={structure.admin_pct ?? 0} step={0.01}
            onChange={(v) => setStructure((s) => ({ ...s, admin_pct: v }))} />
          <NumField label="Admin fees ($M)" value={structure.admin_fees ?? 0} step={5}
            onChange={(v) => setStructure((s) => ({ ...s, admin_fees: v }))} />
        </div>
      </div>

      <h3 className="mb-2 mt-6 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
        Enterprise value · terminal EBITDA × exit multiple, correlated, with a stress regime
      </h3>
      <div className="flex flex-wrap gap-x-5 gap-y-4">
        <Field label="Base EBITDA $M" title={label || undefined}>
          <NumCell value={sim.base_ebitda} step={25} className="w-28 py-1.5"
            onChange={(v) => setSim((s) => ({ ...s, base_ebitda: v }))} />
        </Field>
        {knob("horizon_years", "Horizon yrs", 0.25)}
        {knob("ebitda_vol", "EBITDA vol", 0.01)}
        {knob("mean_reversion", "Mean reversion", 0.05)}
        {knob("stress_prob", "Stress prob", 0.05)}
        {knob("stress_vol", "Stress vol", 0.01)}
        {knob("stress_log_drift", "Stress drift", 0.05)}
        {knob("base_multiple", "Multiple (normal)", 0.25)}
        {knob("distress_multiple", "Multiple (stress)", 0.25)}
        {knob("multiple_vol", "Multiple vol", 0.01)}
        {knob("corr", "EBITDA × multiple corr", 0.05)}
        <Field label="Accrual yrs (override)"
          title="unsecured interest accrues to the petition date; a number here replaces that">
          <NumCell value={sim.accrual_years} step={0.05} className="w-28 py-1.5"
            onChange={(v) => setSim((s) => ({ ...s, accrual_years: v }))} />
        </Field>
      </div>
      <div className="mt-5">
        <Button variant="primary" onClick={onRun} disabled={!!running}>
          {running === "base" ? "Simulating…" : "Re-run"}
        </Button>
      </div>
    </Section>
  );
}
