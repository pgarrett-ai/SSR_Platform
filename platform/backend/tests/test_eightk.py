"""capstack.eightk: per-CIK 8-K item enumeration off the EDGAR submissions API.
The network seam (_fetch_submissions) is monkeypatched — no network. Verifies 8-K
filtering, items parsing, the items_unknown flag (empty items -> "unknown", never
"no trigger"), earliest-1.03 petition detection, and crisis-item triggers."""
import pytest

import app.capstack.eightk as eightk


def _submissions():
    # parallel arrays as EDGAR returns them; mixes 8-K with a 10-K and an items-less 8-K
    return {"filings": {"recent": {
        "form":            ["8-K",        "10-K",       "8-K",        "8-K"],
        "filingDate":      ["2024-05-01", "2024-03-15", "2023-11-20", "2023-06-01"],
        "accessionNumber": ["0000000000-24-000002", "0000000000-24-000001",
                            "0000000000-23-000009", "0000000000-23-000004"],
        "items":           ["4.02,2.02",  "",           "1.03",       ""],
    }}}


def _patch(monkeypatch):
    monkeypatch.setattr(eightk, "_fetch_submissions", lambda cik: _submissions())


def test_list_filters_8k_and_parses_items(monkeypatch):
    _patch(monkeypatch)
    rows = eightk.list_8k_items("0000320193")
    assert len(rows) == 3                                  # the 10-K is excluded
    assert rows[0]["items"] == ["4.02", "2.02"]
    assert rows[0]["source_url"].endswith("000000000024000002/0000000000-24-000002-index.htm")
    # the items-less 8-K is flagged unknown, not empty-no-trigger
    last = rows[-1]
    assert last["items"] is None and last["items_unknown"] is True


def test_since_filter(monkeypatch):
    _patch(monkeypatch)
    rows = eightk.list_8k_items("320193", since="2024-01-01")
    assert [r["filing_date"] for r in rows] == ["2024-05-01"]


def test_petition_filing_earliest_103(monkeypatch):
    _patch(monkeypatch)
    pf = eightk.petition_filing("320193")
    assert pf["date"] == "2023-11-20"
    assert pf["accession"] == "0000000000-23-000009"
    assert pf["source_url"] is not None


def test_petition_filing_none_when_no_103(monkeypatch):
    monkeypatch.setattr(eightk, "_fetch_submissions", lambda cik: {"filings": {"recent": {
        "form": ["8-K"], "filingDate": ["2024-01-01"],
        "accessionNumber": ["x-24-1"], "items": ["2.02"]}}})
    assert eightk.petition_filing("1") is None


def test_fetch_submissions_day_caches(monkeypatch, tmp_path):
    # PR-B: the per-CIK day-cache collapses repeated fetches to one EDGAR hit per CIK per day
    # (SEC 10 req/s fair-access). The raw seam _http_get_submissions is the only network call.
    calls = {"n": 0}

    def fake_http(cik):
        calls["n"] += 1
        return {"filings": {"recent": {"form": [], "filingDate": [],
                                       "accessionNumber": [], "items": []}}}

    monkeypatch.setattr(eightk, "_http_get_submissions", fake_http)
    monkeypatch.setattr(eightk, "_SUBMISSIONS_CACHE_DIR", tmp_path)
    eightk._fetch_submissions("320193")
    eightk._fetch_submissions("320193")
    assert calls["n"] == 1   # second call served from the same-day cache


def test_list_8k_follows_overflow_pages(monkeypatch):
    # a petition older than the recent window lives in a `filings.files` overflow page —
    # list_8k_items must follow it so petition_filing doesn't falsely report "no petition".
    def subs(cik):
        return {"filings": {
            "recent": {"form": ["8-K"], "filingDate": ["2026-01-01"],
                       "accessionNumber": ["a-26-1"], "items": ["2.02"]},
            "files": [{"name": "CIK-submissions-001.json", "filingTo": "2024-12-31"}]}}

    def overflow(name):
        return {"form": ["8-K"], "filingDate": ["2021-05-05"],
                "accessionNumber": ["a-21-9"], "items": ["1.03"]}   # old bankruptcy filing

    monkeypatch.setattr(eightk, "_fetch_submissions", subs)
    monkeypatch.setattr(eightk, "_fetch_submission_file", overflow)
    dates = [r["filing_date"] for r in eightk.list_8k_items("320193")]
    assert "2026-01-01" in dates and "2021-05-05" in dates       # recent + overflow both included
    assert eightk.petition_filing("320193")["date"] == "2021-05-05"


def test_list_8k_overflow_failure_propagates(monkeypatch):
    # a persistent overflow-page failure must NOT be swallowed into a false "no trigger" —
    # it propagates so recovery_case/crisis surface petition_error/trigger_error ("unknown").
    def subs(cik):
        return {"filings": {"recent": {"form": [], "filingDate": [], "accessionNumber": [], "items": []},
                            "files": [{"name": "x.json"}]}}

    def boom(name):
        raise RuntimeError("edgar down")

    monkeypatch.setattr(eightk, "_fetch_submissions", subs)
    monkeypatch.setattr(eightk, "_fetch_submission_file", boom)
    with pytest.raises(RuntimeError):
        eightk.list_8k_items("1")


def test_list_8k_skips_overflow_older_than_since(monkeypatch):
    def subs(cik):
        return {"filings": {"recent": {"form": [], "filingDate": [],
                                       "accessionNumber": [], "items": []},
                            "files": [{"name": "old.json", "filingTo": "2020-12-31"}]}}
    fetched = {"n": 0}

    def overflow(name):
        fetched["n"] += 1
        return {"form": [], "filingDate": [], "accessionNumber": [], "items": []}

    monkeypatch.setattr(eightk, "_fetch_submissions", subs)
    monkeypatch.setattr(eightk, "_fetch_submission_file", overflow)
    eightk.list_8k_items("1", since="2025-01-01")
    assert fetched["n"] == 0   # overflow page entirely older than `since` is skipped, not fetched


def test_crisis_triggers_and_unknown(monkeypatch):
    _patch(monkeypatch)
    trig = eightk.crisis_triggers("320193")
    # the 4.02 filing is a trigger; the two items-less 8-Ks come back as unknown
    firsts = {r["filing_date"]: r for r in trig}
    assert firsts["2024-05-01"]["triggers"] == {"4.02": "non-reliance / restatement"}
    assert firsts["2023-06-01"]["triggers"] is None and firsts["2023-06-01"]["items_unknown"]
    # the 1.03-only filing is not a crisis trigger
    assert "2023-11-20" not in firsts
