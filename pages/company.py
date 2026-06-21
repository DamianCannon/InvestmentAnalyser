import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import yfinance as yf

from db.database import get_session_factory, get_engine
from db.models import (
    Company, FinancialPeriod, FinancialLineItem, LineType,
    SharePrice, ForecastEntry, CompanyNote, TradingUpdateAnalysis,
    Document, CompanyType, PeriodType
)
from metrics.calculator import build_metrics_table, LINE_ITEM_LABELS, METRIC_LABELS

st.title("Company Analysis")

engine = get_engine()
Session = get_session_factory(engine)


def _find_closest_price(price_map, date_str):
    if not price_map:
        return (None, None)
    dates = sorted(price_map.keys())
    for d in reversed(dates):
        if d <= date_str:
            return price_map[d]
    return price_map[dates[0]]


with Session() as session:
    tickers = [c.ticker for c in session.query(Company).order_by(Company.ticker).all()]

if not tickers:
    st.info("No companies in database. Add companies via the Universe or Ingest pages.")
    st.stop()

ticker = st.selectbox("Select company", tickers)

with Session() as session:
    company = session.get(Company, ticker)
    if not company:
        st.stop()

    periods = (
        session.query(FinancialPeriod)
        .filter_by(ticker=ticker)
        .order_by(FinancialPeriod.period_end_date)
        .all()
    )

    line_items_by_period: dict[int, dict[str, float]] = {}
    for period in periods:
        items = (
            session.query(FinancialLineItem)
            .filter_by(period_id=period.id, is_canonical=True)
            .all()
        )
        line_items_by_period[period.id] = {
            item.line_type.value: item.value for item in items
        }

    prices = session.query(SharePrice).filter_by(ticker=ticker).order_by(SharePrice.price_date).all()
    price_map = {p.price_date: (p.close, p.market_cap) for p in prices}

    prices_by_period: dict[int, tuple] = {}
    for period in periods:
        if period.period_end_date in price_map:
            prices_by_period[period.id] = price_map[period.period_end_date]
        else:
            closest = _find_closest_price(price_map, period.period_end_date)
            prices_by_period[period.id] = closest

    forecasts = (
        session.query(ForecastEntry)
        .filter_by(ticker=ticker)
        .order_by(ForecastEntry.entered_at.desc())
        .all()
    )
    forecast_eps_growth = None
    for f in forecasts:
        if f.metric_type == "eps_growth" and f.value is not None:
            forecast_eps_growth = f.value
            break

    notes = (
        session.query(CompanyNote)
        .filter_by(ticker=ticker)
        .order_by(CompanyNote.created_at.desc())
        .all()
    )

    latest_doc = (
        session.query(Document)
        .filter_by(ticker=ticker)
        .order_by(Document.uploaded_at.desc())
        .first()
    )
    trading_analyses = []
    if latest_doc:
        trading_analyses = (
            session.query(TradingUpdateAnalysis)
            .filter_by(document_id=latest_doc.id)
            .all()
        )

    col1, col2, col3 = st.columns(3)
    col1.metric("Ticker", ticker)
    col2.metric("Company", company.name)
    col3.metric("Type", company.company_type.value.replace("_", " ").title())


tab_metrics, tab_charts, tab_trading, tab_forecast, tab_notes = st.tabs(
    ["Metrics", "Charts", "Trading Updates", "Forecasts", "Notes"]
)

with tab_metrics:
    if not periods:
        st.info("No financial data yet. Upload documents via Ingest.")
    else:
        col_headers, row_labels, data = build_metrics_table(
            periods, line_items_by_period, prices_by_period, forecast_eps_growth
        )

        pct_rows = {
            "EBITDA Margin %", "Operating Margin %", "Net Margin %",
            "Revenue Growth %", "EPS Growth % (Reported)", "EPS Growth % (Adjusted)",
            "Revenue CAGR 3yr", "Revenue CAGR 5yr", "EPS CAGR 3yr", "EPS CAGR 5yr",
            "FCF Yield %", "Dividend Yield %", "ROCE %", "Incremental ROCE %",
            "Cash Conversion %", "NAV Premium / Discount %",
        }

        def fmt_val(label, v):
            if v is None:
                return "—"
            if label in pct_rows:
                return f"{v * 100:.1f}%"
            if abs(v) > 1_000_000:
                return f"{v / 1_000_000:.1f}m"
            if abs(v) > 1_000:
                return f"{v / 1_000:.1f}k"
            return f"{v:.2f}"

        table_data = {}
        for label in row_labels:
            row = data.get(label, {})
            table_data[label] = {col: fmt_val(label, row.get(col)) for col in col_headers}

        df = pd.DataFrame(table_data).T
        df.columns = col_headers

        selected_metric = st.selectbox(
            "Click metric to chart:",
            ["— Select —"] + row_labels,
            key="metric_select"
        )

        st.dataframe(df, use_container_width=True, height=600)

        if selected_metric and selected_metric != "— Select —":
            row = data.get(selected_metric, {})
            chart_data = {col: row.get(col) for col in col_headers if row.get(col) is not None}
            if chart_data:
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=list(chart_data.keys()),
                    y=list(chart_data.values()),
                    mode="lines+markers",
                    name=selected_metric,
                ))
                fig.update_layout(title=selected_metric, height=350)
                st.plotly_chart(fig, use_container_width=True)

with tab_charts:
    if not periods:
        st.info("No financial data yet.")
    else:
        def get_series(key):
            xs, ys = [], []
            for p in periods:
                v = line_items_by_period.get(p.id, {}).get(key)
                if v is not None:
                    xs.append(p.period_end_date)
                    ys.append(v)
            return xs, ys

        annual = [p for p in periods if p.period_type == PeriodType.annual]
        interim = [p for p in periods if p.period_type == PeriodType.interim]

        def bar_series(ps, key, name, color=None):
            xs = [p.period_end_date for p in ps]
            ys = [line_items_by_period.get(p.id, {}).get(key) for p in ps]
            return go.Bar(x=xs, y=ys, name=name, marker_color=color)

        st.subheader("Revenue / EBITDA / Operating Profit")
        fig1 = go.Figure()
        fig1.add_trace(bar_series(annual, "revenue", "Revenue (Annual)", "#4C72B0"))
        fig1.add_trace(bar_series(annual, "ebitda", "EBITDA (Annual)", "#55A868"))
        fig1.add_trace(bar_series(annual, "operating_profit", "Op Profit (Annual)", "#C44E52"))
        fig1.add_trace(bar_series(interim, "revenue", "Revenue (Interim)", "#4C72B0"))
        fig1.update_layout(barmode="group", height=350)
        st.plotly_chart(fig1, use_container_width=True)

        st.subheader("EPS: Reported vs Adjusted")
        fig2 = go.Figure()
        x, y = get_series("eps_reported")
        fig2.add_trace(go.Scatter(x=x, y=y, name="EPS Reported", mode="lines+markers"))
        x, y = get_series("eps_adjusted")
        fig2.add_trace(go.Scatter(x=x, y=y, name="EPS Adjusted", mode="lines+markers", line=dict(dash="dash")))
        fig2.update_layout(height=300)
        st.plotly_chart(fig2, use_container_width=True)

        st.subheader("Net Debt / EBITDA")
        fig3 = go.Figure()
        xs = [p.period_end_date for p in annual]
        nd = [line_items_by_period.get(p.id, {}).get("net_debt") for p in annual]
        eb = [line_items_by_period.get(p.id, {}).get("ebitda") for p in annual]
        ratio = [n / e if n is not None and e and e != 0 else None for n, e in zip(nd, eb)]
        fig3.add_trace(go.Bar(x=xs, y=ratio, name="Net Debt / EBITDA"))
        fig3.update_layout(height=300)
        st.plotly_chart(fig3, use_container_width=True)

        st.subheader("ROCE & Incremental ROCE")
        from metrics.calculator import calculate_growth_metrics, calculate_period_metrics
        pd_dicts = []
        for p in annual:
            li = line_items_by_period.get(p.id, {})
            pr, mc = prices_by_period.get(p.id, (None, None))
            pm = calculate_period_metrics(li, pr, mc)
            pd_dicts.append({"period": p, "line_items": li, "period_metrics": pm})
        pd_dicts = calculate_growth_metrics(pd_dicts)
        fig4 = go.Figure()
        fig4.add_trace(go.Scatter(
            x=[d["period"].period_end_date for d in pd_dicts],
            y=[d["period_metrics"].get("roce") for d in pd_dicts],
            name="ROCE", mode="lines+markers"
        ))
        fig4.add_trace(go.Scatter(
            x=[d["period"].period_end_date for d in pd_dicts],
            y=[d.get("incremental_roce") for d in pd_dicts],
            name="Incremental ROCE", mode="lines+markers", line=dict(dash="dot")
        ))
        fig4.update_layout(height=300)
        st.plotly_chart(fig4, use_container_width=True)

        st.subheader("Cash Flow")
        fig5 = go.Figure()
        for key, label, color in [
            ("operating_cash_flow", "Operating CF", "#4C72B0"),
            ("capex", "Capex", "#C44E52"),
            ("free_cash_flow", "FCF", "#55A868"),
        ]:
            x, y = get_series(key)
            fig5.add_trace(go.Bar(x=x, y=y, name=label, marker_color=color))
        fig5.update_layout(barmode="group", height=300)
        st.plotly_chart(fig5, use_container_width=True)

        st.subheader("DPS & Dividend Cover")
        fig6 = make_subplots(specs=[[{"secondary_y": True}]])
        x, y = get_series("dps")
        fig6.add_trace(go.Bar(x=x, y=y, name="DPS"), secondary_y=False)
        cover = []
        cx = [p.period_end_date for p in periods]
        for p in periods:
            li = line_items_by_period.get(p.id, {})
            eps = li.get("eps_reported")
            dps = li.get("dps")
            cover.append(eps / dps if eps and dps and dps != 0 else None)
        fig6.add_trace(go.Scatter(x=cx, y=cover, name="Cover", mode="lines+markers"), secondary_y=True)
        fig6.update_layout(height=300)
        st.plotly_chart(fig6, use_container_width=True)

        st.subheader("Cash Conversion %")
        fig7 = go.Figure()
        cc = []
        ccx = [p.period_end_date for p in annual]
        for p in annual:
            li = line_items_by_period.get(p.id, {})
            ocf = li.get("operating_cash_flow")
            op = li.get("operating_profit")
            cc.append(ocf / op * 100 if ocf and op and op != 0 else None)
        fig7.add_trace(go.Bar(x=ccx, y=cc, name="Cash Conversion %"))
        fig7.update_layout(height=300)
        st.plotly_chart(fig7, use_container_width=True)

        if company.company_type == CompanyType.investment_trust:
            st.subheader("NAV Premium / Discount %")
            fig8 = go.Figure()
            navd = []
            navx = [p.period_end_date for p in periods]
            for p in periods:
                li = line_items_by_period.get(p.id, {})
                nav = li.get("nav_per_share")
                pr, _ = prices_by_period.get(p.id, (None, None))
                navd.append((pr - nav) / nav * 100 if pr and nav and nav != 0 else None)
            fig8.add_trace(go.Scatter(x=navx, y=navd, name="NAV Disc/Prem %", mode="lines+markers"))
            fig8.update_layout(height=300)
            st.plotly_chart(fig8, use_container_width=True)

with tab_trading:
    if not trading_analyses:
        st.info("No trading update analysis available. Ingest a trading update via the Ingest page.")
    else:
        st.subheader(f"Latest trading update analysis")
        direction_colors = {
            "ahead": "🟢",
            "inline": "🟡",
            "behind": "🔴",
            "unclear": "⚪",
        }
        rows = []
        for ta in trading_analyses:
            rows.append({
                "Metric": ta.metric_type,
                "Direction": direction_colors.get(ta.guidance_direction.value if ta.guidance_direction else "", "⚪")
                            + " " + (ta.guidance_direction.value.upper() if ta.guidance_direction else "UNCLEAR"),
                "Quote": ta.guidance_text or "",
                "Implied Value": ta.extrapolated_value,
                "Discrepancy": "⚠️" if ta.flagged_discrepancy else "",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True)

with tab_forecast:
    st.subheader("Consensus EPS Forecast Log")
    with Session() as session:
        forecasts_all = (
            session.query(ForecastEntry)
            .filter_by(ticker=ticker)
            .order_by(ForecastEntry.entered_at.desc())
            .all()
        )
    if forecasts_all:
        rows = [
            {
                "Date": f.entered_at.strftime("%Y-%m-%d %H:%M") if f.entered_at else "",
                "Metric": f.metric_type,
                "Value": f.value,
                "Period": f.period_end_date,
                "Notes": f.source_notes or "",
            }
            for f in forecasts_all
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
    else:
        st.info("No forecasts logged yet.")

    with st.form("add_forecast"):
        st.write("**Add forecast entry**")
        c1, c2, c3 = st.columns(3)
        metric = c1.selectbox("Metric", ["eps_growth", "eps_reported", "eps_adjusted", "revenue"])
        value = c2.number_input("Value", step=0.1)
        period = c3.text_input("Period end date (YYYY-MM-DD)")
        notes = st.text_input("Source / notes")
        if st.form_submit_button("Save"):
            with Session() as session:
                session.add(ForecastEntry(
                    ticker=ticker,
                    metric_type=metric,
                    value=value,
                    period_end_date=period or None,
                    source_notes=notes or None,
                ))
                session.commit()
            st.rerun()

with tab_notes:
    st.subheader("Investment Notes")
    with st.form("add_note"):
        note_text = st.text_area("Add a note", height=100)
        if st.form_submit_button("Save note"):
            if note_text.strip():
                with Session() as session:
                    session.add(CompanyNote(ticker=ticker, note_text=note_text.strip()))
                    session.commit()
                st.rerun()

    with Session() as session:
        all_notes = (
            session.query(CompanyNote)
            .filter_by(ticker=ticker)
            .order_by(CompanyNote.created_at.desc())
            .all()
        )
    for note in all_notes:
        ts = note.created_at.strftime("%Y-%m-%d %H:%M") if note.created_at else ""
        st.markdown(f"**{ts}**")
        st.markdown(note.note_text)
        st.divider()
