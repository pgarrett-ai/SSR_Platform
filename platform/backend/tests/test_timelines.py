"""Phase 4.6: leverage timeline / maturity wall / what-changed (pure; no network)."""
from __future__ import annotations

from app.capstack.timelines import leverage_timeline, maturity_wall, what_changed
from app.schemas import CitedValue, DebtInstrument, ForensicTableRow


def _cv(v):
    return CitedValue(value=v) if v is not None else None


def _row(fy, debt=None, cash=None, ebitda=None, fcf=None, revenue=None):
    return ForensicTableRow(fiscal_year=fy, total_debt=_cv(debt), cash=_cv(cash),
                            ebitda=_cv(ebitda), free_cash_flow=_cv(fcf), revenue=_cv(revenue))


def test_leverage_timeline():
    pts = leverage_timeline([_row(2024, debt=30e9, ebitda=5e9), _row(2025, debt=28e9)])
    assert pts[0].leverage == 6.0
    assert pts[1].leverage is None          # no EBITDA -> no ratio, not a crash


def _inst(name, face, maturity):
    return DebtInstrument(instrument=name, outstanding=_cv(face), maturity=maturity)


def test_maturity_wall_single_years_and_ranges():
    wall = maturity_wall([
        _inst("TL", 970e6, "February 2028"),
        _inst("Notes", 500e6, "2028"),
        _inst("EETC", 3e9, "maturing from 2026 to 2028"),   # spread over 3 years
        _inst("No date", 100e6, None),                       # dropped
    ])
    by_year = {b.year: b for b in wall}
    assert set(by_year) == {2026, 2027, 2028}
    assert by_year[2026].face == 1e9
    assert by_year[2028].face == 970e6 + 500e6 + 1e9
    assert "EETC" in by_year[2026].instruments and "TL" in by_year[2028].instruments


def test_what_changed_directions_and_threshold():
    rows = [_row(2024, debt=30e9, cash=1e9, ebitda=5e9, fcf=1e9, revenue=54e9),
            _row(2025, debt=28e9, cash=1.05e9, ebitda=3.7e9, fcf=-0.7e9, revenue=54.5e9)]
    changes = what_changed(rows)
    by_metric = {c.metric: c for c in changes}
    assert by_metric["Total reported debt"].direction == "better"      # debt down = better
    assert by_metric["EBITDA (proxy)"].direction == "worse"            # ebitda down = worse
    assert by_metric["Free cash flow"].direction == "worse"
    assert by_metric["Leverage (debt/EBITDA)"].direction == "worse"    # 6.0x -> 7.6x
    assert "Revenue" not in by_metric                                  # +0.9% < 2% threshold
    assert changes[0].delta_pct == max(abs(c.delta_pct) for c in changes) or \
           abs(changes[0].delta_pct) == max(abs(c.delta_pct) for c in changes)  # sorted by |delta|
    assert what_changed(rows[:1]) == []                                # one year -> nothing
