import React from "react";
import CitedNumber from "./CitedNumber.jsx";
import { Section, Stat } from "../ui/index.jsx";

// A live Chapter 11 case frames every recovery number below it: statutory clocks run from
// the petition date (the 8-K Item 1.03 filing date stands in for the docket date). A
// confirmed plan ends the case, so its clocks stop.

const DAY = 86400000;
const EXCLUSIVITY_DAYS = 120;   // §1121 plan exclusivity; courts routinely extend it
const SOLICITATION_DAYS = 180;  // acceptance / solicitation period

const utc = (iso) => new Date(`${iso}T00:00:00Z`);

function addDays(iso, n) {
  const d = utc(iso);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export default function CaseCard({ ch11 }) {
  const petition = ch11.petition_date;
  const confirmed = ch11.confirmed_at;
  const days = Math.max(Math.floor(((confirmed ? utc(confirmed) : Date.now()) - utc(petition)) / DAY), 0);

  const clock = (label, window) => {
    const left = window - days;
    return (
      <Stat bare label={label} value={addDays(petition, window)}
        sub={left >= 0 ? `${left}d remaining` : `passed ${-left}d ago unless extended`} />
    );
  };

  return (
    <Section title="Chapter 11 case"
      subtitle={confirmed ? `plan confirmed ${confirmed}` : "statutory clocks from the petition date"}>
      <div className="flex flex-wrap gap-x-10 gap-y-4">
        <Stat bare label="Petition date"
          value={ch11.source ? <CitedNumber cv={{ display: petition, citation: ch11.source }} /> : petition} />
        <Stat bare label={confirmed ? "Petition to confirmation" : "Time in case"}
          value={`${(days / 30.44).toFixed(1)} mo`} sub={`${days} days`} />
        {confirmed
          ? <Stat bare label="Plan confirmed" value={confirmed} />
          : <>
              {clock(`Plan exclusivity (${EXCLUSIVITY_DAYS}d)`, EXCLUSIVITY_DAYS)}
              {clock(`Solicitation (${SOLICITATION_DAYS}d)`, SOLICITATION_DAYS)}
            </>}
      </div>
    </Section>
  );
}
