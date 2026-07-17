import React from "react";
import { fetchTelegraph } from "../api.js";
import { useAsync } from "../cache.js";
import CitedNumber from "./CitedNumber.jsx";
import { markOnly } from "./DocSearch.jsx";
import { Badge, Loading } from "../ui/index.jsx";

// Bank triage + filing telegraph (Moyer ch. 8): where the bank sits when trouble
// starts, and the five disclosure/behavior tells that a filing is being telegraphed.
// One payload — bank strip on top, the five signal rows below.

const BANK_TONE = {
  filing_pretext: "high",
  security_grab: "watch",
  undersecured_watch: "watch",
  waiver_path: "ok",
  no_bank_debt: "info",
  coverage_unknown: "neutral",
};
const DOT = { on: "bg-rose-400", off: "bg-emerald-400/70", unknown: "bg-slate-600" };

function coverageLabel(cov) {
  if (!cov) return null;
  if (cov.basis === "liquidation")
    return `covered ${cov.coverage_pct}% on orderly liquidation (net proceeds $${cov.net_proceeds_mm?.toLocaleString()}M vs claim $${cov.bank_claim_mm?.toLocaleString()}M)`;
  const p = cov.points || [];
  return `covered ${p[0]?.coverage_pct}% @4× / ${p[1]?.coverage_pct}% @6× EBITDA (claim $${cov.bank_claim_mm?.toLocaleString()}M)`;
}

export default function Telegraph({ ticker, years }) {
  const { data, loading, error } = useAsync(
    `telegraph:${ticker}:${years}`, () => fetchTelegraph(ticker, years), [ticker, years]);

  if (loading) return <Loading />;
  if (error) return <div className="text-xs text-rose-300">{error}</div>;
  if (!data) return null;
  const bank = data.bank || {};
  const tel = data.telegraph || {};
  const signals = tel.signals || [];
  const score = tel.score || {};

  return (
    <div>
      {/* bank-position strip */}
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        {bank.available === false ? (
          <span className="text-slate-500">{bank.note}</span>
        ) : (
          <>
            <Badge tone={BANK_TONE[bank.state] || "neutral"}>
              {(bank.state || "").replace(/_/g, " ")}
            </Badge>
            <span className="text-slate-400">{bank.state_note}</span>
            {bank.drawn_total && (
              <span className="text-slate-500">
                drawn <CitedNumber cv={bank.drawn_total} className="text-slate-200" />
              </span>
            )}
            {bank.undrawn_total && (
              <span className="text-slate-500">
                undrawn <CitedNumber cv={bank.undrawn_total} className="text-slate-200" />
              </span>
            )}
            {bank.coverage && (
              <span className="text-slate-500"
                title={(bank.notes || []).join(" · ")}>{coverageLabel(bank.coverage)}</span>
            )}
            {(bank.rows || []).some((r) => r.secured_source === "name-heuristic") && (
              <span className="text-[10px] uppercase tracking-wide text-slate-500"
                title="secured status inferred from the instrument name — not tagged in XBRL">
                name-heuristic
              </span>
            )}
          </>
        )}
      </div>

      {/* headline: unknowns stay out of the denominator */}
      <div className="mb-2 text-sm text-slate-200"
        title="signals reading unknown are excluded from the denominator">
        <span className="font-semibold">{score.on ?? "—"} of {score.evaluable ?? "—"}</span>
        <span className="text-slate-400"> filing tells telegraphed</span>
        <span className="ml-2 text-xs text-slate-500">as of {tel.as_of}</span>
      </div>

      {/* the five signal rows */}
      <div className="divide-y divide-ink-800">
        {signals.map((s) => (
          <div key={s.key} className="py-2">
            <div className="flex items-center gap-2 text-sm">
              <span className={`h-2 w-2 rounded-full ${DOT[s.state] || DOT.unknown}`}
                title={s.state} />
              <span className="text-slate-200">{s.label}</span>
              <span className="text-[10px] uppercase tracking-wide text-slate-500">{s.state}</span>
              {s.amount && <CitedNumber cv={s.amount} className="text-slate-300" />}
            </div>
            <div className="ml-4 mt-0.5 text-xs text-slate-400" title={s.assumption}>
              {s.detail}
            </div>
            {(s.evidence || []).map((h, i) => (
              <div key={i} className="ml-4 mt-1 rounded-md border border-ink-700 px-2 py-1 text-xs text-slate-400">
                <Badge>{h.source_kind === "mdna" ? "MD&A" : h.source_kind}</Badge>
                <span className="ml-1 font-mono text-[10px] text-slate-500">{h.date}</span>
                <span className="ml-2"
                  dangerouslySetInnerHTML={{ __html: markOnly(h.snippet || "") }} />
              </div>
            ))}
          </div>
        ))}
      </div>

      {/* unscored context */}
      <div className="mt-2 text-[11px] text-slate-500">
        {tel.context?.covenants?.length > 0 && (
          <div>
            covenant context:{" "}
            {tel.context.covenants.slice(0, 4).map((c, i) => (
              <span key={i} className="mr-2">
                {c.kind || "covenant"} {c.threshold || ""}{c.test_frequency ? ` (${c.test_frequency})` : ""}
              </span>
            ))}
            {tel.context.covenants.length > 4 && `+${tel.context.covenants.length - 4} more`}
          </div>
        )}
        {tel.context?.note}
      </div>
    </div>
  );
}
