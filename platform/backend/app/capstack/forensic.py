"""The forensic 'where is the cash coming from?' test (brief §6a).

Builds a year-by-year cited table (debt, cash, FCF, capex, AP, inventory, DPO, EBITDA, OCF) and
auto-detects divergences that hint at off-balance-sheet financing:

  * AP / DPO climbing faster than COGS/revenue   -> reverse factoring / supplier finance
  * EBITDA growing while operating cash flow lags -> receivables financing / working-capital games
  * Cash rising without a rise in reported debt   -> factoring / securitization / sale-leaseback / VIE
  * Sustained negative free cash flow             -> liquidity runway / cash burn (distress timing)

Each flag points the analyst at the footnote/MD&A to read next.
"""
from __future__ import annotations

from typing import Optional

from ..edgar.facts import (
    FinancialSeries,
    YearFacts,
    cited_metric,
    derived_value,
    fmt_days,
    fmt_money_millions,
    raw_value,
)
from ..schemas import CitedValue, ForensicFlag, ForensicTableRow

_DEBT_COMPONENTS = ("lt_debt_noncurrent", "lt_debt_current", "short_term_debt")


# ---- derived metrics -------------------------------------------------------


def total_debt(yf: YearFacts) -> tuple[Optional[float], list[str]]:
    parts, total = [], 0.0
    found = False
    for key in _DEBT_COMPONENTS:
        v = raw_value(yf, key)
        if v is not None:
            total += v
            found = True
            parts.append(f"{key}={fmt_money_millions(v)}")
    return (total if found else None), parts


def _ebitda(yf: YearFacts) -> Optional[float]:
    oi, da = raw_value(yf, "operating_income"), raw_value(yf, "d_and_a")
    if oi is None or da is None:
        return None
    return oi + da


def _fcf(yf: YearFacts) -> Optional[float]:
    ocf, capex = raw_value(yf, "operating_cash_flow"), raw_value(yf, "capex")
    if ocf is None or capex is None:
        return None
    return ocf - capex


def _dpo(yf: YearFacts) -> tuple[Optional[float], Optional[str]]:
    ap = raw_value(yf, "accounts_payable")
    if ap is None:
        return None, None
    cogs = raw_value(yf, "cogs")
    denom, denom_label = (cogs, "COGS") if cogs else (raw_value(yf, "operating_expenses"), "opex")
    if not denom:
        return None, None
    return ap / denom * 365.0, denom_label


# ---- table -----------------------------------------------------------------


def build_forensic_table(series: FinancialSeries) -> list[ForensicTableRow]:
    cik = series.cik
    rows: list[ForensicTableRow] = []
    for yf in series.years:
        td_val, td_parts = total_debt(yf)
        ebitda_val = _ebitda(yf)
        fcf_val = _fcf(yf)
        dpo_val, denom_label = _dpo(yf)

        td_cv = (
            derived_value(td_val, " + ".join(td_parts), fmt_money_millions(td_val),
                          note="Sum of reported debt components (each cited).")
            if td_val is not None else None
        )
        ebitda_cv = (
            derived_value(ebitda_val,
                          f"operating_income ({fmt_money_millions(raw_value(yf, 'operating_income'))}) "
                          f"+ D&A ({fmt_money_millions(raw_value(yf, 'd_and_a'))})",
                          fmt_money_millions(ebitda_val),
                          note="Proxy EBITDA = operating income + D&A.")
            if ebitda_val is not None else None
        )
        fcf_cv = (
            derived_value(fcf_val,
                          f"OCF ({fmt_money_millions(raw_value(yf, 'operating_cash_flow'))}) "
                          f"- capex ({fmt_money_millions(raw_value(yf, 'capex'))})",
                          fmt_money_millions(fcf_val))
            if fcf_val is not None else None
        )
        dpo_cv = (
            derived_value(dpo_val,
                          f"accounts_payable / {denom_label} x 365",
                          fmt_days(dpo_val),
                          note=f"Days payable outstanding (denominator: {denom_label}).")
            if dpo_val is not None else None
        )

        rows.append(
            ForensicTableRow(
                fiscal_year=yf.fiscal_year,
                period_end=yf.period_end.isoformat(),
                total_debt=td_cv,
                cash=cited_metric(yf, "cash", cik),
                free_cash_flow=fcf_cv,
                capex=cited_metric(yf, "capex", cik),
                accounts_payable=cited_metric(yf, "accounts_payable", cik),
                inventory=cited_metric(yf, "inventory", cik),
                revenue=cited_metric(yf, "revenue", cik),
                cogs=cited_metric(yf, "cogs", cik),
                ebitda=ebitda_cv,
                operating_cash_flow=cited_metric(yf, "operating_cash_flow", cik),
                dpo=dpo_cv,
            )
        )
    return rows


# ---- flags -----------------------------------------------------------------


def _pct_change(curr: Optional[float], prev: Optional[float]) -> Optional[float]:
    if curr is None or prev is None or prev == 0:
        return None
    return (curr - prev) / abs(prev)


def detect_flags(series: FinancialSeries) -> list[ForensicFlag]:
    if len(series.years) < 2:
        return []
    flags: list[ForensicFlag] = []
    first, last = series.years[0], series.years[-1]
    prev, curr = series.years[-2], series.years[-1]

    # 1) AP / DPO climbing faster than the business -> reverse factoring / supplier finance
    ap_change = _pct_change(raw_value(curr, "accounts_payable"), raw_value(first, "accounts_payable"))
    rev_change = _pct_change(raw_value(curr, "revenue"), raw_value(first, "revenue"))
    dpo_first, _ = _dpo(first)
    dpo_last, denom_label = _dpo(last)
    dpo_delta_days = (dpo_last - dpo_first) if (dpo_first and dpo_last) else None
    # Two independent supplier-finance tells: (a) DPO that has *crept up* far faster than the
    # business, and (b) an *absolute* DPO level far above the ~30-60 day norm — the hallmark of
    # reverse factoring even when it's being unwound (e.g. payment terms stretched via a bank
    # facility booked as trade payables). AP-outrunning alone is too noisy, so it needs a DPO climb.
    ap_outruns = (ap_change is not None and rev_change is not None and ap_change - rev_change > 0.15)
    dpo_climbs_strong = (dpo_delta_days is not None and dpo_delta_days >= 10)
    dpo_climbs_mild = (dpo_delta_days is not None and dpo_delta_days >= 5)
    dpo_elevated = (dpo_last is not None and dpo_last > 90)
    dpo_extreme = (dpo_last is not None and dpo_last > 150)
    if dpo_climbs_strong or (ap_outruns and dpo_climbs_mild) or dpo_elevated:
        if dpo_elevated:
            severity = "high" if dpo_extreme else "watch"
            lead = (
                f"DPO ~{round(dpo_last)} days (FY{last.fiscal_year}) vs a ~30-60 day norm — "
                f"consistent with supplier finance booked in trade payables."
            )
        else:
            severity = "high" if (ap_outruns and dpo_climbs_strong) else "watch"
            lead = (
                f"AP {round((ap_change or 0)*100):+d}% vs revenue {round((rev_change or 0)*100):+d}% "
                f"(FY{first.fiscal_year}-FY{last.fiscal_year}); DPO up ~{round(dpo_delta_days)} days — "
                f"payables stretching well ahead of the business."
            )
        flags.append(ForensicFlag(
            flag_type="ap_outrunning_revenue",
            severity=severity,
            fiscal_year=last.fiscal_year,
            metrics={
                "ap_growth_pct": round((ap_change or 0) * 100, 1) if ap_change is not None else None,
                "revenue_growth_pct": round((rev_change or 0) * 100, 1) if rev_change is not None else None,
                "dpo_first": round(dpo_first, 1) if dpo_first else None,
                "dpo_last": round(dpo_last, 1) if dpo_last else None,
                "dpo_change_days": round(dpo_delta_days, 1) if dpo_delta_days else None,
                "window": f"FY{first.fiscal_year}->FY{last.fiscal_year}",
            },
            narrative=lead + " Included in economic debt — behaves like a callable facility.",
            pointer="AP footnote; supplier-finance disclosure (ASU 2022-04, ASC 405-50); MD&A liquidity.",
        ))

    # 2) EBITDA growing while operating cash flow lags -> earnings not converting to cash
    cum_ebitda = sum(v for v in (_ebitda(y) for y in series.years) if v is not None)
    cum_ocf = sum(v for v in (raw_value(y, "operating_cash_flow") for y in series.years) if v is not None)
    if cum_ebitda > 0 and cum_ocf is not None:
        conversion = cum_ocf / cum_ebitda
        ebitda_up = _pct_change(_ebitda(curr), _ebitda(prev))
        if conversion < 0.6 or (ebitda_up is not None and ebitda_up > 0.1
                                and (_pct_change(raw_value(curr, "operating_cash_flow"),
                                                 raw_value(prev, "operating_cash_flow")) or 0) < 0):
            flags.append(ForensicFlag(
                flag_type="ebitda_vs_ocf_divergence",
                severity="watch",
                fiscal_year=last.fiscal_year,
                metrics={
                    "cumulative_ebitda": fmt_money_millions(cum_ebitda),
                    "cumulative_ocf": fmt_money_millions(cum_ocf),
                    "cash_conversion": round(conversion, 2),
                },
                narrative=(
                    f"Cumulative proxy EBITDA {fmt_money_millions(cum_ebitda)} converted to "
                    f"{round(conversion*100)}% in OCF ({fmt_money_millions(cum_ocf)}). "
                    f"Covenants are EBITDA-based; default is cash-based."
                ),
                pointer="CF working-capital lines; receivables securitization / transfer-of-financial-assets "
                        "footnotes; revenue-recognition policy.",
            ))

    # 3) Cash rising without a rise in reported debt -> off-balance-sheet financing
    cash_change = _pct_change(raw_value(curr, "cash"), raw_value(prev, "cash"))
    td_curr, _ = total_debt(curr)
    td_prev, _ = total_debt(prev)
    debt_change = _pct_change(td_curr, td_prev)
    if cash_change is not None and cash_change > 0.25 and (debt_change is None or debt_change <= 0.02):
        flags.append(ForensicFlag(
            flag_type="cash_up_no_debt",
            severity="info",
            fiscal_year=last.fiscal_year,
            metrics={
                "cash_change_pct": round(cash_change * 100, 1),
                "debt_change_pct": round((debt_change or 0) * 100, 1),
            },
            narrative=(
                f"Cash +{round(cash_change*100)}% in FY{curr.fiscal_year}, reported debt "
                f"{('down ' + str(abs(round((debt_change or 0)*100))) + '%') if (debt_change or 0) < 0 else 'roughly flat'} — "
                f"trace the source (factoring, securitization, sale-leaseback, VIE)."
            ),
            pointer="CF financing / investing sections; VIE, securitization and sale-leaseback footnotes.",
        ))

    # 4) Sustained negative free cash flow -> liquidity runway / cash burn
    fcf_last = _fcf(last)
    if fcf_last is not None and fcf_last < 0:
        cash_last = raw_value(last, "cash")
        runway_note = ""
        if cash_last and fcf_last < 0:
            months = cash_last / (abs(fcf_last) / 12.0)
            runway_note = f" Cash covers ~{months:.0f} months at this burn."
        flags.append(ForensicFlag(
            flag_type="negative_fcf_burn",
            severity="watch" if fcf_last > -1e9 else "high",
            fiscal_year=last.fiscal_year,
            metrics={
                "free_cash_flow": fmt_money_millions(fcf_last),
                "cash": fmt_money_millions(cash_last) if cash_last else None,
            },
            narrative=(
                f"Free cash flow {fmt_money_millions(fcf_last)} in FY{last.fiscal_year} "
                f"(OCF − capex).{runway_note}"
            ),
            pointer="MD&A liquidity & capital resources; revolver availability / borrowing base; "
                    "maturity schedule.",
        ))

    return flags
