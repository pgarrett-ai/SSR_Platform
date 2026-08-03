import React from "react";
import Header from "../components/Header.jsx";
import { EmptyState, Section } from "../ui/index.jsx";
import ForensicTable from "../components/ForensicTable.jsx";
import FlagCard from "../components/FlagCard.jsx";
import SourcesPanel from "../components/SourcesPanel.jsx";
import EconomicDebtBridge from "../components/EconomicDebtBridge.jsx";
import EbitdaBuild from "../components/EbitdaBuild.jsx";
import DebtScheduleTable from "../components/DebtScheduleTable.jsx";
import ObsFindings from "../components/ObsFindings.jsx";
import SubsidiariesList from "../components/SubsidiariesList.jsx";
import CovenantPackages from "../components/CovenantPackages.jsx";
import CapacityCard from "../components/CapacityCard.jsx";
import CreationLadder from "../components/CreationLadder.jsx";
import TradeBasis from "../components/TradeBasis.jsx";
import RefiWall from "../components/RefiWall.jsx";
import Telegraph from "../components/Telegraph.jsx";
import OptionsCard from "../components/OptionsCard.jsx";
import LiquidityCalendar from "../components/LiquidityCalendar.jsx";
import DocSearch from "../components/DocSearch.jsx";
import HoldersPanel from "../components/HoldersPanel.jsx";

// Overview data + Run Live + progress log live in the shell (App.jsx) — this page renders
// whatever snapshot the shell holds for the routed ticker.
export default function CapitalPage({ ticker, health, overview, onRunLive }) {
  const flags = overview?.forensic_flags || [];
  // Badge for LLM-derived sections: fresh run → none; spliced prior snapshot → "prior
  // analysis" (info); no key → "needs API key" (high); toggled off → "LLM off" (watch).
  const llmBadge = overview?.header?.llm_enabled
    ? null
    : overview?.llm_fallback_note?.startsWith("Prior")
      ? { label: "prior analysis", tone: "info", title: overview.llm_fallback_note }
      : health?.llm_key_set === false
        ? { label: "needs API key", tone: "high", title: "set ANTHROPIC_API_KEY in platform/.env" }
        : { label: "LLM off", tone: "watch", title: "LLM analysis toggled off — enable it in the sidebar" };

  return (
    <div>
      {!overview && (
        <EmptyState
          hint="a live run against EDGAR takes ~3 minutes with the LLM on"
          action={onRunLive ? { label: "Run live ↻", onClick: onRunLive } : undefined}
        >
          No analysis loaded for {ticker} yet.
        </EmptyState>
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
            collapsible
            title="Economic debt bridge"
            subtitle="reported debt → economic (adjusted) debt"
            badge={llmBadge}
          >
            <EconomicDebtBridge bridge={overview.economic_debt_bridge} />
          </Section>

          {overview.ebitda_build && (
            <Section
              collapsible
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
            collapsible
            title="As-reported debt schedule"
            subtitle={`${overview.debt_schedule?.length || 0} instruments${
              overview.debt_schedule_asof ? ` · as of ${overview.debt_schedule_asof}` : ""
            } · amounts from XBRL dimensions`}
          >
            <DebtScheduleTable instruments={overview.debt_schedule} />
            <RefiWall ticker={ticker} years={overview.header?.years || 3} />
          </Section>

          <Section
            collapsible
            title="Creation-multiple ladder"
            subtitle="cumulative claims through each class ÷ EBITDA, at face and at market"
          >
            <CreationLadder ticker={ticker} years={overview.header?.years || 3} />
          </Section>

          <Section
            collapsible
            title="Trade basis"
            subtitle="effective cost basis per matched quote — accrued, trading flat, claim per 100, cash-at-risk"
          >
            <TradeBasis ticker={ticker} years={overview.header?.years || 3} />
          </Section>

          <Section
            collapsible
            defaultOpen={false}
            title="Liquidity event calendar"
            subtitle="coupons and maturities over the next 24 months vs cash + undrawn credit"
          >
            <LiquidityCalendar events={overview.liquidity_events} note={overview.liquidity_events_note} />
          </Section>

          <Section
            collapsible
            title="Credit capacity"
            subtitle="can the structure repay itself internally? cash-sweep model, leverage × growth grid, cycle stress"
          >
            <CapacityCard ticker={ticker} years={overview.header?.years || 3} />
          </Section>

          <Section
            collapsible
            title="Bank position & filing telegraph"
            subtitle="where the bank sits when trouble starts, and the five tells a filing is being telegraphed"
          >
            <Telegraph ticker={ticker} years={overview.header?.years || 3} />
          </Section>

          <Section
            collapsible
            title="Company options"
            subtitle="buy back debt, exchange it, or sell assets — what the clock, cash, and covenants allow"
          >
            <OptionsCard ticker={ticker} years={overview.header?.years || 3} />
          </Section>

          <Section collapsible title="Forensic cash-vs-debt test" subtitle="XBRL facts by fiscal year · flags fire on divergences">
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
            collapsible
            defaultOpen={false}
            id="obs"
            title="Off-balance-sheet findings"
            subtitle={`${overview.obs_items?.length || 0} items extracted from footnotes & MD&A`}
            badge={llmBadge}
          >
            <ObsFindings items={overview.obs_items} />
          </Section>

          {overview.subsidiaries?.length > 0 && (
            <Section
              collapsible
              defaultOpen={false}
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
            collapsible
            id="covenants"
            title="Covenants & creditors"
            subtitle={`${overview.covenants?.length || 0} agreement famil${
              (overview.covenants?.length || 0) === 1 ? "y" : "ies"
            } from EX-10.x / EX-4.x · RP-basket build + liens headroom at the end`}
            badge={llmBadge}
          >
            <CovenantPackages
              covenants={overview.covenants}
              instruments={overview.debt_schedule}
              overview={overview}
            />
          </Section>

          <HoldersPanel ticker={ticker} />

          <Section collapsible defaultOpen={false} title="Sources" subtitle={`${overview.sources.length} filings analyzed`}>
            <SourcesPanel sources={overview.sources} />
          </Section>
        </>
      )}
    </div>
  );
}
