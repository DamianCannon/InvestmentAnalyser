from __future__ import annotations
from typing import Optional


def _safe_div(a, b) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _growth(current, previous) -> Optional[float]:
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / abs(previous)


def _cagr(start, end, years) -> Optional[float]:
    if start is None or end is None or start <= 0 or years <= 0:
        return None
    return (end / start) ** (1 / years) - 1


def calculate_period_metrics(
    line_items: dict[str, Optional[float]],
    price: Optional[float] = None,
    market_cap: Optional[float] = None,
) -> dict[str, Optional[float]]:
    """Calculate all single-period metrics from a flat dict of line_type -> value."""
    r = {}
    rev = line_items.get("revenue")
    ebitda = line_items.get("ebitda")
    op_profit = line_items.get("operating_profit")
    net_profit = line_items.get("net_profit")
    eps_rep = line_items.get("eps_reported")
    eps_adj = line_items.get("eps_adjusted")
    net_debt = line_items.get("net_debt")
    op_cf = line_items.get("operating_cash_flow")
    capex = line_items.get("capex")
    fcf = line_items.get("free_cash_flow")
    dps = line_items.get("dps")
    cap_emp = line_items.get("capital_employed")
    interest = line_items.get("interest_expense")
    nav = line_items.get("nav_per_share")

    if fcf is None and op_cf is not None and capex is not None:
        fcf = op_cf - abs(capex)

    r["ebitda_margin"] = _safe_div(ebitda, rev)
    r["operating_margin"] = _safe_div(op_profit, rev)
    r["net_margin"] = _safe_div(net_profit, rev)
    r["net_debt_ebitda"] = _safe_div(net_debt, ebitda)
    r["interest_cover"] = _safe_div(op_profit, interest)
    r["cash_conversion"] = _safe_div(op_cf, op_profit)
    r["dividend_cover"] = _safe_div(eps_rep, dps)
    r["roce"] = _safe_div(op_profit, cap_emp)

    if price and eps_rep:
        r["pe_reported"] = _safe_div(price, eps_rep)
    else:
        r["pe_reported"] = None

    if price and eps_adj:
        r["pe_adjusted"] = _safe_div(price, eps_adj)
    else:
        r["pe_adjusted"] = None

    if price and dps:
        r["dividend_yield"] = _safe_div(dps, price)
    else:
        r["dividend_yield"] = None

    if market_cap and fcf:
        r["fcf_yield"] = _safe_div(fcf, market_cap)
    else:
        r["fcf_yield"] = None

    if market_cap and net_debt is not None and ebitda:
        ev = market_cap + net_debt
        r["ev_ebitda"] = _safe_div(ev, ebitda)
    else:
        r["ev_ebitda"] = None

    if price and nav:
        r["nav_discount"] = _safe_div(price - nav, nav)
    else:
        r["nav_discount"] = None

    return r


def calculate_growth_metrics(
    periods: list[dict],
) -> list[dict]:
    """Given a list of period dicts (sorted oldest-first) each containing line_items,
    returns same list enriched with growth fields."""
    enriched = []
    for i, period in enumerate(periods):
        g = dict(period)
        prev = periods[i - 1] if i > 0 else None
        li = period.get("line_items", {})
        prev_li = prev.get("line_items", {}) if prev else {}

        g["revenue_growth"] = _growth(li.get("revenue"), prev_li.get("revenue"))
        g["ebitda_growth"] = _growth(li.get("ebitda"), prev_li.get("ebitda"))
        g["op_profit_growth"] = _growth(li.get("operating_profit"), prev_li.get("operating_profit"))
        g["eps_growth_reported"] = _growth(li.get("eps_reported"), prev_li.get("eps_reported"))
        g["eps_growth_adjusted"] = _growth(li.get("eps_adjusted"), prev_li.get("eps_adjusted"))

        if prev and li.get("operating_profit") and prev_li.get("capital_employed"):
            delta_op = (li.get("operating_profit") or 0) - (prev_li.get("operating_profit") or 0)
            prev_prev = periods[i - 2] if i > 1 else None
            prev_prev_li = prev_prev.get("line_items", {}) if prev_prev else {}
            delta_cap = (prev_li.get("capital_employed") or 0) - (prev_prev_li.get("capital_employed") or 0)
            g["incremental_roce"] = _safe_div(delta_op, delta_cap) if delta_cap else None
        else:
            g["incremental_roce"] = None

        n = len(periods)
        if i == n - 1 and n >= 4:
            start3 = periods[i - 3]["line_items"]
            g["revenue_cagr_3yr"] = _cagr(start3.get("revenue"), li.get("revenue"), 3)
            g["eps_cagr_3yr"] = _cagr(start3.get("eps_reported"), li.get("eps_reported"), 3)
        else:
            g["revenue_cagr_3yr"] = None
            g["eps_cagr_3yr"] = None

        if i == n - 1 and n >= 6:
            start5 = periods[i - 5]["line_items"]
            g["revenue_cagr_5yr"] = _cagr(start5.get("revenue"), li.get("revenue"), 5)
            g["eps_cagr_5yr"] = _cagr(start5.get("eps_reported"), li.get("eps_reported"), 5)
        else:
            g["revenue_cagr_5yr"] = None
            g["eps_cagr_5yr"] = None

        enriched.append(g)
    return enriched


def build_metrics_table(
    periods: list,
    line_items_by_period: dict[int, dict[str, float]],
    prices_by_period: dict[int, tuple[float, float]],
    forecast_eps_growth: Optional[float] = None,
) -> tuple[list[str], list[str], dict]:
    """
    Build the full metrics table.
    periods: list of FinancialPeriod ORM objects sorted oldest-first
    line_items_by_period: {period.id -> {line_type_str -> value}}
    prices_by_period: {period.id -> (close, market_cap)}
    Returns (col_headers, row_labels, data[row_label][col_header])
    """
    period_dicts = []
    for p in periods:
        li = line_items_by_period.get(p.id, {})
        price, mcap = prices_by_period.get(p.id, (None, None))
        pm = calculate_period_metrics(li, price, mcap)
        period_dicts.append({
            "period": p,
            "line_items": li,
            "period_metrics": pm,
            "price": price,
            "market_cap": mcap,
        })

    period_dicts = calculate_growth_metrics(period_dicts)

    col_headers = [p["period"].period_end_date for p in period_dicts]

    all_keys = list(LINE_ITEM_LABELS.keys()) + list(METRIC_LABELS.keys())
    row_labels = [LINE_ITEM_LABELS.get(k) or METRIC_LABELS.get(k) or k for k in all_keys]

    data = {}
    for key, label in {**LINE_ITEM_LABELS, **METRIC_LABELS}.items():
        row = {}
        for pd_ in period_dicts:
            col = pd_["period"].period_end_date
            if key in LINE_ITEM_LABELS:
                val = pd_["line_items"].get(key)
            else:
                val = pd_["period_metrics"].get(key) or pd_.get(key)
            row[col] = val
        data[label] = row

    if forecast_eps_growth is not None:
        for pd_ in period_dicts:
            col = pd_["period"].period_end_date
            pe = pd_["period_metrics"].get("pe_reported")
            if pe and forecast_eps_growth != 0:
                data.setdefault("PEG Ratio", {})[col] = _safe_div(pe, forecast_eps_growth * 100)
            else:
                data.setdefault("PEG Ratio", {})[col] = None

    return col_headers, list(data.keys()), data


LINE_ITEM_LABELS = {
    "revenue": "Revenue",
    "ebitda": "EBITDA",
    "operating_profit": "Operating Profit",
    "net_profit": "Net Profit",
    "eps_reported": "EPS (Reported)",
    "eps_adjusted": "EPS (Adjusted)",
    "net_debt": "Net Debt",
    "operating_cash_flow": "Operating Cash Flow",
    "capex": "Capex",
    "free_cash_flow": "Free Cash Flow",
    "dps": "DPS",
    "shares_outstanding": "Shares Outstanding",
    "capital_employed": "Capital Employed",
    "interest_expense": "Interest Expense",
    "nav_per_share": "NAV per Share",
}

METRIC_LABELS = {
    "revenue_growth": "Revenue Growth %",
    "ebitda_margin": "EBITDA Margin %",
    "operating_margin": "Operating Margin %",
    "net_margin": "Net Margin %",
    "eps_growth_reported": "EPS Growth % (Reported)",
    "eps_growth_adjusted": "EPS Growth % (Adjusted)",
    "revenue_cagr_3yr": "Revenue CAGR 3yr",
    "revenue_cagr_5yr": "Revenue CAGR 5yr",
    "eps_cagr_3yr": "EPS CAGR 3yr",
    "eps_cagr_5yr": "EPS CAGR 5yr",
    "pe_reported": "P/E (Reported)",
    "pe_adjusted": "P/E (Adjusted)",
    "ev_ebitda": "EV/EBITDA",
    "net_debt_ebitda": "Net Debt / EBITDA",
    "fcf_yield": "FCF Yield %",
    "dividend_yield": "Dividend Yield %",
    "dividend_cover": "Dividend Cover",
    "roce": "ROCE %",
    "incremental_roce": "Incremental ROCE %",
    "interest_cover": "Interest Cover",
    "cash_conversion": "Cash Conversion %",
    "nav_discount": "NAV Premium / Discount %",
}
