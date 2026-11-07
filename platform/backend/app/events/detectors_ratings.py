"""17g-7 rating-default detector (the monthly ratings_refresh job).

Reuses hazard.labels' SD harvest wholesale: the same Fitch D/RD + Moody's C Rule 17g-7
corporate rating histories that seed the model's real default LABELS become timeline
EVENTS. `events_from_sd_rows` is pure; `refresh_ratings` fetches the CSVs through the one
feed seam and inserts idempotently.

Only default actions (issuer D/RD/C) become events. Multi-notch downgrades are not
detected: that needs a per-agency rating-scale order this module does not carry."""
from __future__ import annotations

import datetime as dt

from . import edgar_feed as feed
from .types import Event

RECENT_DAYS = 60


def events_from_sd_rows(rows: list[dict]) -> list[Event]:
    """Pure: SD default rows ({cik, name, filed, source}) -> rating_default Events.

    cik padded via feed.pad_cik; confidence 0.9 because a name-matched CIK
    (labels._cik_by_name) can misfire on a namesake; the synthetic accession
    'ratings:{source}:{cik}:{filed}' keys the idempotent dedupe (no EDGAR accession exists)."""
    out: list[Event] = []
    for r in rows:
        cik = feed.pad_cik(r["cik"])
        src = r.get("source") or "ratings"
        filed = r["filed"]
        out.append(Event(
            cik=cik, event_type="rating_default", subtype=src,
            severity=5, confidence=0.9, occurred_at=filed,
            source="ratings", source_form="17g-7",
            accession_no=f"ratings:{src}:{cik}:{filed}", source_url=None,
            title=f"Rating default ({src}) — {r.get('name') or cik}", payload={}))
    return out


def refresh_ratings() -> int:
    """Monthly job: download the NRSROs' Rule 17g-7 corporate histories, extract the
    earliest default event per CIK, insert idempotently. Reuses labels._SD_SOURCES +
    sd_events_from_frame + _cik_by_name wholesale; every byte leaves through feed."""
    import io

    import pandas as pd

    from ..core.db import session_scope
    from ..hazard import labels
    from .store import insert_events

    lookup = labels._cik_by_name()
    by_cik: dict[str, dict] = {}
    for url, ratings, type_pattern, src in labels._SD_SOURCES:
        df = pd.read_csv(io.BytesIO(feed.get_bytes(url, timeout=300.0)), dtype=str)
        rows, _unmatched = labels.sd_events_from_frame(df, lookup, ratings, type_pattern, src)
        for ev in rows:                     # earliest default action per obligor wins
            # ponytail: the synthetic accession embeds the source agency, so a later
            # monthly run that finds an EARLIER default from the OTHER agency inserts a
            # second rating_default beside the first (old row isn't superseded). Rare —
            # historical default dates are stable; dedupe/supersede only if it shows up.
            cur = by_cik.get(ev["cik"])
            if cur is None or ev["filed"] < cur["filed"]:
                by_cik[ev["cik"]] = ev
    events = events_from_sd_rows(sorted(by_cik.values(), key=lambda e: e["filed"]))
    # a default years old found by a first run is history, not news: only recent actions
    # get a detection stamp, so the feed's "newly detected" ordering stays honest
    recent_from = (dt.date.today() - dt.timedelta(days=RECENT_DAYS)).isoformat()
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)    # naive UTC, the store's convention
    with session_scope() as session:
        return (insert_events(session, [e for e in events if e.occurred_at < recent_from],
                              detected_at=None)
                + insert_events(session, [e for e in events if e.occurred_at >= recent_from],
                                detected_at=now))
