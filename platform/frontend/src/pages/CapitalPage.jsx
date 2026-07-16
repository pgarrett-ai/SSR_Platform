import React from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import Header from "../components/Header.jsx";
import { ACCENT, INK, Section, chartTooltipStyle } from "../ui/index.jsx";
import ForensicTable from "../components/ForensicTable.jsx";
import FlagCard from "../components/FlagCard.jsx";
import SourcesPanel from "../components/SourcesPanel.jsx";
import EconomicDebtBridge from "../components/EconomicDebtBridge.jsx";
import EbitdaBuild from "../components/EbitdaBuild.jsx";
import DebtScheduleTable from "../components/DebtScheduleTable.jsx";
import ObsFindings from "../components/ObsFindings.jsx";
import SubsidiariesList from "../components/SubsidiariesList.jsx";
import CovenantPackages from "../components/CovenantPackages.jsx";
import CreationLadder from "../components/CreationLadder.jsx";
import DocSearch from "../components/DocSearch.jsx";
import HoldersPanel from "../components/HoldersPanel.jsx";
import MdnaReader from "../components/MdnaReader.jsx";

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
          <CartesianGrid stroke={INK[600]} strokeDasharray="3 3" />
          <XAxis dataKey="year" tick={{ fill: "#94a3b8", fontSize: 10 }} />
          <YAxis tick={{ fill: "#94a3b8", fontSize: 10 }} />
          <Tooltip
            contentStyle={chartTooltipStyle}
            formatter={(v, _n, p) => [`$${v}B — ${p.payload.instruments}`, null]}
          />
          <Bar dataKey="face" fill={ACCENT} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// Overview data + Run Live + progress log live in the shell (App.jsx) — this page renders
// whatever snapshot the shell holds for the routed ticker.
export default function CapitalPage({ ticker, health, overview }) {
  const flags = overview?.forensic_flags || [];
  // Badge for LLM-derived sections: fresh run → none; spliced prior snapshot → "prior
  // analysis"; nothing to show → "LLM off". Full note (with date) rides in warnings.
  const llmBadge = overview?.header?.llm_enabled
    ? null
    : overview?.llm_fallback_note?.startsWith("Prior")
      ? "prior analysis"
      : health?.llm_key_set === false
        ? "needs API key"
        : "LLM off";

  return (
    <div>
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
            title="Economic debt bridge"
            subtitle="reported debt → economic (adjusted) debt"
            badge={llmBadge}
          >
            <EconomicDebtBridge bridge={overview.economic_debt_bridge} />
          </Section>

          {overview.ebitda_build && (
            <Section
              title="EBITDA build"
              subtitle="net income → EBITDA, plus the issuer's covenant add-backs"
            >
              <EbitdaBuild
                build={overview.ebitda_build}
                economicDebt={overview.economic_debt_bridge?.economic_debt}
              />
            </Section>
          )}

          <Section
            title="As-reported debt schedule"
            subtitle={`${overview.debt_schedule?.length || 0} instruments${
              overview.debt_schedule_asof ? ` · as of ${overview.debt_schedule_asof}` : ""
            } · amounts from XBRL dimensions`}
          >
            <DebtScheduleTable instruments={overview.debt_schedule} />
            {overview.maturity_wall?.length > 0 && <MaturityWall wall={overview.maturity_wall} />}
          </Section>

          <Section
            title="Creation-multiple ladder"
            subtitle="cumulative claims through each class ÷ EBITDA, at face and at market (Moyer)"
          >
            <CreationLadder ticker={ticker} years={overview.header?.years || 3} />
          </Section>

          <Section title="Forensic cash-vs-debt test" subtitle="XBRL facts by fiscal year · flags fire on divergences">
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
            id="obs"
            title="Off-balance-sheet findings"
            subtitle={`${overview.obs_items?.length || 0} items extracted from footnotes & MD&A`}
            badge={llmBadge}
          >
            <ObsFindings items={overview.obs_items} />
          </Section>

          {overview.subsidiaries?.length > 0 && (
            <Section
              title="Legal entities"
              subtitle={`${overview.subsidiaries.length} entities from Exhibit 21 · obligors matched from XBRL`}
            >
              <SubsidiariesList
                subsidiaries={overview.subsidiaries}
                guarantorNotes={(overview.covenants || [])
                  .filter((c) => c.guarantors)
                  .map((c) => `${c.family_label || c.agreement_type}: ${c.guarantors}`)}
              />
            </Section>
          )}

          <DocSearch ticker={ticker} />

          <Section
            id="covenants"
            title="Covenants & creditors"
            subtitle={`${overview.covenants?.length || 0} agreement famil${
              (overview.covenants?.length || 0) === 1 ? "y" : "ies"
            } from EX-10.x / EX-4.x`}
            badge={llmBadge}
          >
            <CovenantPackages
              covenants={overview.covenants}
              instruments={overview.debt_schedule}
            />
          </Section>

          <HoldersPanel ticker={ticker} />

          <Section id="mdna" title="MD&A" subtitle="management's discussion, per filing period">
            <MdnaReader ticker={ticker} />
          </Section>

          <Section title="Sources" subtitle={`${overview.sources.length} filings analyzed`}>
            <SourcesPanel sources={overview.sources} />
          </Section>
        </>
      )}
    </div>
  );
}
