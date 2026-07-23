"""C8 sponsor-support card: deterministic assembler (admin_agent lender flag, free) + the
DEF 14A LLM seam (ownership %, verbatim-quote-gated) + the /sponsor endpoint merge with
live 13D/G event rows. No network, no live LLM — extract_structured is monkeypatched."""
from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.capstack.sponsor import OwnerRaw, RptRaw, build_sponsor, extract_sponsor_ownership
from app.schemas import CovenantPackage

# ---- build_sponsor (pure, deterministic) -------------------------------------------


def test_build_sponsor_lcid_hero():
    covenants = [CovenantPackage(admin_agent="Ayar Third Investment Company")]
    owners = [OwnerRaw(name="Public Investment Fund", pct=57.0, shares=None,
                       quote="PIF beneficially owns approximately 57% of our common stock")]
    rpts = [RptRaw(counterparty="Ayar Third Investment Company", description="DDTL facility",
                   amount_usd=1_000_000_000.0, is_lender=True,
                   quote="Ayar, an affiliate of PIF, is the lender under our DDTL")]
    sp = build_sponsor(covenants, owners, rpts)
    assert sp.has_sponsor is True
    # sponsor_name attributes to the owner PIF's % is cited under, not the Ayar lender name.
    assert sp.sponsor_name == "Public Investment Fund"
    assert sp.ownership_pct.value == 57.0
    assert sp.ownership_pct.citation.quote == owners[0].quote
    assert sp.related_party_lender == "Ayar Third Investment Company"
    assert sp.lender_source == "related-party-transactions footnote"


def test_build_sponsor_empty():
    owners = [OwnerRaw(name="Index Funds Inc.", pct=5.0, shares=None, quote="owns 5%")]
    sp = build_sponsor([], owners, [])
    assert sp.has_sponsor is False


def test_llm_off_deterministic_lender():
    covenants = [CovenantPackage(admin_agent="Ayar Third Investment Company")]
    sp = build_sponsor(covenants, [], [])
    assert sp.has_sponsor is True
    # No qualifying owner and no RPT lender -> sponsor_name falls back to the admin-agent
    # lender's own name (owner-less lender-only naming).
    assert sp.sponsor_name == "Ayar Third Investment Company"
    assert sp.related_party_lender == "Ayar Third Investment Company"
    assert sp.lender_source == "covenant admin agent"
    assert sp.ownership_pct is None


def test_build_sponsor_no_crash_empty_counterparty():
    """rpt is_lender=True with empty counterparty, no qualifying owner, no admin_agent —
    the name-fallback chain must not IndexError on an empty lenders list (finding 2)."""
    rpts = [RptRaw(counterparty="", description="DDTL facility", amount_usd=None,
                   is_lender=True, quote="an affiliate is the lender under our DDTL")]
    sp = build_sponsor([], [], rpts)
    assert sp.has_sponsor is True
    assert sp.sponsor_name is None


# ---- extract_sponsor_ownership (LLM seam, monkeypatched) ---------------------------


class _FakeFiling:
    accession_no = "0001234567-26-000099"
    form = "DEF 14A"
    filing_date = "2026-04-01"
    url = "https://www.sec.gov/fake-def14a-index.htm"

    def text(self):
        return ("Item. Security ownership of certain beneficial owners.\n" * 5 +
               "Certain relationships and related person transactions.\n" * 5)


class _FakeFilings:
    def latest(self, n):
        return _FakeFiling()


class _FakeCompany:
    def get_filings(self, form=None):
        return _FakeFilings()


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """Sponsor extraction caches per-doc to disk (covenants.py pattern) — point it at a
    throwaway dir so tests never write into the real (git-tracked) app/cache/ tree."""
    from app.core import cache as cache_mod
    monkeypatch.setattr(cache_mod, "CACHE_DIR", tmp_path)


def test_quote_gate(monkeypatch):
    from app.capstack import sponsor as sponsor_mod

    def _fake_extract(**kw):
        return {"owners": [{"name": "Nobody", "pct": 10.0, "shares": None, "quote": ""}],
                "rpts": [{"counterparty": "X", "description": "y", "amount_usd": None,
                         "is_lender": True, "quote": "   "}]}

    monkeypatch.setattr(sponsor_mod, "extract_structured", _fake_extract)
    owners, rpts, err = extract_sponsor_ownership(_FakeCompany())
    assert owners == []
    assert rpts == []
    assert err is None


def test_extract_keeps_quoted_rows(monkeypatch):
    from app.capstack import sponsor as sponsor_mod

    def _fake_extract(**kw):
        return {"owners": [{"name": "Public Investment Fund", "pct": 57.0, "shares": None,
                            "quote": "PIF owns approximately 57%"}],
                "rpts": [{"counterparty": "Ayar Third Investment Company",
                         "description": "DDTL", "amount_usd": 1e9, "is_lender": True,
                         "quote": "Ayar is the lender under our DDTL"}]}

    monkeypatch.setattr(sponsor_mod, "extract_structured", _fake_extract)
    owners, rpts, err = extract_sponsor_ownership(_FakeCompany())
    assert err is None
    assert len(owners) == 1 and owners[0].name == "Public Investment Fund"
    assert len(rpts) == 1 and rpts[0].is_lender is True


def test_extract_returns_empty_when_llm_off(monkeypatch):
    from app.capstack import sponsor as sponsor_mod

    monkeypatch.setattr(sponsor_mod, "extract_structured", lambda **kw: None)
    owners, rpts, err = extract_sponsor_ownership(_FakeCompany())
    assert owners == [] and rpts == [] and err is None


# ---- GET /api/company/{ticker}/sponsor (cache + live event merge) ------------------

client = TestClient(__import__("app.main", fromlist=["app"]).app).__enter__()

CIK = "0009990002"   # test-only padded CIK


def _fake_overview_with_sponsor():
    from app.schemas import CitedValue, IssuerHeader, Overview, SponsorSupport
    return Overview(
        header=IssuerHeader(ticker="SPNX", years=3, cik=CIK),
        sponsor=SponsorSupport(
            has_sponsor=True, sponsor_name="Ayar Third Investment Company",
            ownership_pct=CitedValue(value=57.0, display="57.0%"),
            related_party_lender="Ayar Third Investment Company",
            lender_source="related-party-transactions footnote",
        ),
    )


@pytest.fixture(scope="module", autouse=True)
def _seed_stake_events():
    from app import models_events as me
    from app.core.db import session_scope
    with session_scope() as s:
        for i in range(2):
            s.add(me.Event(
                cik=CIK, event_type="stake_13d", subtype=None, severity=3, confidence=1.0,
                occurred_at=dt.datetime(2026, 1, 1 + i), detected_at=dt.datetime(2026, 1, 1 + i),
                source="edgar", source_form="SC 13D", accession_no=None,
                source_url="https://www.sec.gov/x-index.htm", title="SC 13D", payload={},
                dedupe_key=f"sponsor-test-{i}",
            ))


def test_endpoint_merges_live_stakes(monkeypatch):
    from app import main
    monkeypatch.setattr(main, "load_overview", lambda ticker, years: _fake_overview_with_sponsor())
    r = client.get("/api/company/SPNX/sponsor")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sponsor"]["has_sponsor"] is True
    assert body["sponsor"]["related_party_lender"] == "Ayar Third Investment Company"
    assert len(body["stake_filings"]) == 2
    assert body["cik"] == CIK


def test_endpoint_empty_state_when_uncached():
    r = client.get("/api/company/NOSUCHTICKERZZZ/sponsor")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sponsor"] is None
    assert body["note"]
