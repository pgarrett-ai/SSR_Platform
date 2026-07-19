"""Phase 6 event framework: dataclasses/dedupe, registry routing, idempotent store,
poller end-to-end with every network seam monkeypatched. No network, temp DB only.
CIKs everywhere are the canonical 10-digit zero-padded form (Interface Contract)."""
from __future__ import annotations

import datetime as dt
import json

import pytest

from app.events.types import Event, FilingMeta

CIK = "0000000777"


def _ev(**kw):
    base = dict(cik=CIK, event_type="bankruptcy", subtype="1.03", severity=5,
                confidence=1.0, occurred_at="2026-01-02", source="edgar",
                source_form="8-K", accession_no="0000000000-26-000001",
                source_url=None, title="t", payload={})
    base.update(kw)
    return Event(**base)


def test_dedupe_key_stable_and_delegates_to_models_events():
    from app.models_events import make_dedupe_key
    assert _ev().dedupe_key == make_dedupe_key("0000000000-26-000001", "bankruptcy", "1.03")
    assert _ev(subtype="2.04").dedupe_key != _ev().dedupe_key
    assert _ev(accession_no="x-2").dedupe_key != _ev().dedupe_key
    assert len(_ev().dedupe_key) == 64


def test_event_bounds_enforced():
    with pytest.raises(ValueError):
        _ev(severity=6)
    with pytest.raises(ValueError):
        _ev(confidence=1.5)
