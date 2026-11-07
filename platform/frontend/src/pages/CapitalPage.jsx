import React from "react";
import { ErrorCard, Loading, Section } from "../ui/index.jsx";
import CitedNumber from "../components/CitedNumber.jsx";
import ProgressLog from "../components/ProgressLog.jsx";
import DebtScheduleTable from "../components/DebtScheduleTable.jsx";
import RefiWall from "../components/RefiWall.jsx";
import LiquidityCalendar from "../components/LiquidityCalendar.jsx";
import EconomicDebtBridge from "../components/EconomicDebtBridge.jsx";
import EbitdaBuild from "../components/EbitdaBuild.jsx";
import CovenantPackages from "../components/CovenantPackages.jsx";
import FlagCard from "../components/FlagCard.jsx";
import ForensicTable from "../components/ForensicTable.jsx";
import ObsFindings from "../components/ObsFindings.jsx";
import SubsidiariesList from "../components/SubsidiariesList.jsx";
import HoldersPanel from "../components/HoldersPanel.jsx";
import SourcesPanel from "../components/SourcesPanel.jsx";

const PART = "mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400";

// Supporting detail under a section's answer, closed until asked for.
function Nested({ title, children }) {
  return (
    <details className="group mt-4 border-t border-ink-700 pt-3">
      <summary className={`cursor-pointer list-none [&::-webkit-details-marker]:hidden ${PART}`}>
        <span aria-hidden className="mr-1 text-[10px] text-slate-500">
          <span className="group-open:hidden">▸</span><span className="hidden group-open:inline">▾</span>
        </span>
        {title}
      </summary>
      <div className="mt-2">{children}</div>
    </details>
  );
}

// What they owe, to whom, when it comes due, and what the documents let the company do
// to its creditors. Renders the overview stream CompanyLayout owns; the debt and
// liquidity headlines sit in the strip above.
export default function CapitalPage({ ticker, overview: stream }) {
  const ov = stream.data;
  if (stream.loading)
    return stream.events.length > 0
      ? <ProgressLog events={stream.events} />
      : <Loading>Loading filings for {ticker}…</Loading>;
  if (stream.error) return <ErrorCard>{stream.error}</ErrorCard>;
  if (!ov) return null;

  const schedule = ov.debt_schedule || [];
  // same rule as the backend's maturity warning: only rows that carry an amount count
  const undated = schedule.filter((d) => !d.maturity
    && (d.outstanding?.value ?? d.principal?.value) != null).length;
  const asOf = ov.debt_schedule_asof;
  // reported debt for the schedule's own period, not the latest fiscal year's
  const reported = asOf && (ov.forensic_table || []).find((r) => r.period_end === asOf);
  const liq = ov.liquidity;
  const families = ov.covenants?.length || 0;
  const flags = ov.forensic_flags || [];
  const otherObs = (ov.obs_items || []).filter((o) => !o.include_in_bridge);
  const subs = ov.subsidiaries || [];
  const sources = ov.sources || [];
  const appendix = [
    otherObs.length && "other disclosures",
    subs.length && "legal entities",
    sources.length && "sources",
  ].filter(Boolean);

  return (
    <div>
      <Section
        collapsible
        title="Debt schedule"
        subtitle={schedule.length > 0 && [
          `${schedule.length} instruments`,
          undated > 0 && `${undated} undated`,
          asOf && `as of ${asOf}`,
        ].filter(Boolean).join(" · ")}
      >
        <DebtScheduleTable
          instruments={schedule}
          asOf={asOf}
          reported={reported?.total_debt}
          reportedLabel={reported && (reported.label || `FY${reported.fiscal_year}`)}
          warnings={ov.warnings}
        />
      </Section>

      <Section
        collapsible
        title="Maturities vs liquidity"
        subtitle={liq?.cash && (
          <>
            vs cash <CitedNumber cv={liq.cash} />
            {liq.undrawn_committed && <> + undrawn <CitedNumber cv={liq.undrawn_committed} /></>}
          </>
        )}
      >
        <RefiWall ticker={ticker} years={ov.header?.years || 3} />
        {ov.liquidity_events?.length > 0 && (
          <Nested title="Coupon & maturity calendar">
            <LiquidityCalendar events={ov.liquidity_events} note={ov.liquidity_events_note} />
          </Nested>
        )}
      </Section>

      <Section collapsible title="Economic debt bridge" subtitle="reported → economic debt">
        <EconomicDebtBridge bridge={ov.economic_debt_bridge} coverage={ov.coverage_chips} />
        {ov.ebitda_build?.lines?.length > 0 && (
          <Nested title="EBITDA used (net income → EBITDA)">
            <EbitdaBuild build={ov.ebitda_build} economicDebt={ov.economic_debt_bridge?.economic_debt} />
          </Nested>
        )}
      </Section>

      <Section
        collapsible
        title="Covenants & LME"
        subtitle={families > 0 && `${families} agreement famil${families === 1 ? "y" : "ies"}`}
      >
        <CovenantPackages covenants={ov.covenants} liens={ov.liens_headroom} rp={ov.rp_basket} />
      </Section>

      <Section collapsible title="Forensic flags" subtitle="cash vs debt by filed period">
        {flags.length > 0 && (
          <div className="mb-5 grid gap-3 md:grid-cols-2">
            {flags.map((f, i) => <FlagCard key={i} flag={f} />)}
          </div>
        )}
        <ForensicTable rows={ov.forensic_table} />
      </Section>

      {appendix.length > 0 && (
        <Section collapsible defaultOpen={false} title="Appendix" subtitle={appendix.join(" · ")}>
          <div className="space-y-6">
            {otherObs.length > 0 && (
              <div>
                <h3 className={PART}>Other disclosures</h3>
                <ObsFindings items={otherObs} />
              </div>
            )}
            {subs.length > 0 && (
              <div>
                <h3 className={PART}>Legal entities & guarantors</h3>
                <SubsidiariesList
                  subsidiaries={subs}
                  guarantorNotes={(ov.covenants || [])
                    .filter((c) => c.guarantors)
                    .map((c) => `${c.family_label || c.agreement_type}: ${c.guarantors}`)}
                />
              </div>
            )}
            <HoldersPanel ticker={ticker} />
            {sources.length > 0 && (
              <div>
                <h3 className={PART}>Sources · {sources.length} filings analyzed</h3>
                <SourcesPanel sources={sources} />
              </div>
            )}
          </div>
        </Section>
      )}
    </div>
  );
}
