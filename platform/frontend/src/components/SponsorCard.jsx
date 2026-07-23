import React from "react";
import { fetchSponsor } from "../api.js";
import { useAsync } from "../cache.js";
import { Section } from "../ui/index.jsx";
import CitedNumber from "./CitedNumber.jsx";

// C8 sponsor-support card: the deterministic related-party-lender flag (free, from the
// covenant admin_agent) + the DEF 14A ownership % (the one LLM seam) merged with the LIVE
// 13D/G stake-filing dates. Empty-states for sponsor-less names — LCID (~57% PIF + Ayar
// DDTL) is the hero case.

export default function SponsorCard({ ticker, years }) {
  const { data } = useAsync(`sponsor:${ticker}:${years}`, () => fetchSponsor(ticker, years), [ticker, years]);
  const sp = data?.sponsor;

  if (!sp || !sp.has_sponsor) {
    return (
      <Section title="Sponsor support" subtitle="controlling holder + related-party credit">
        <div className="text-xs text-slate-500">No controlling sponsor or related-party lender identified.</div>
      </Section>
    );
  }

  return (
    <Section title="Sponsor support" subtitle="the credit factor — control + related-party rescues">
      <div className="text-sm text-slate-200">
        {sp.sponsor_name}
        {sp.ownership_pct && <> · ownership <CitedNumber cv={sp.ownership_pct} /></>}
        {sp.related_party_lender && (
          <> · lender <span className="text-slate-300">{sp.related_party_lender}</span>
            {sp.lender_source && <span className="text-slate-500"> ({sp.lender_source})</span>}
          </>
        )}
      </div>
      {sp.support_items?.length > 0 && (
        <div className="mt-2 space-y-1 text-xs text-slate-400">
          {sp.support_items.map((it, i) => (
            <div key={i}>
              {it.kind}{it.counterparty ? ` — ${it.counterparty}` : ""}: {it.description}{" "}
              <CitedNumber cv={it.amount} />
            </div>
          ))}
        </div>
      )}
      {data.stake_filings?.length > 0 && (
        <div className="mt-2 text-[11px] text-slate-500">
          recent 13D/G: {data.stake_filings.map((e) => e.occurred_at?.slice(0, 10)).filter(Boolean).join(", ")}
        </div>
      )}
    </Section>
  );
}
