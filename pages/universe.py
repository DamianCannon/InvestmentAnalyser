import io
import time

import pandas as pd
import streamlit as st
import yfinance as yf

from db.database import get_engine, get_session_factory
from db.models import Company, CompanyType, SharePrice

st.title("Universe Screener")

engine = get_engine()
Session = get_session_factory(engine)


@st.cache_data(ttl=3600)
def _fetch_yfinance(ticker: str):
    try:
        info = yf.Ticker(ticker).fast_info
        close = getattr(info, "last_price", None)
        market_cap = getattr(info, "market_cap", None)
        return close, market_cap
    except Exception:
        return None, None


tab_import, tab_screen = st.tabs(["Import / Manage", "Screen"])

with tab_import:
    st.subheader("Import LSE instruments CSV")
    st.write(
        "Upload a CSV with columns: `ticker, name, sector, sub_sector, isin, market` "
        "(additional columns are ignored). Tickers should include `.L` suffix for LSE."
    )
    uploaded = st.file_uploader("CSV file", type=["csv"])
    if uploaded:
        df_import = pd.read_csv(io.BytesIO(uploaded.read()))
        df_import.columns = [c.strip().lower().replace(" ", "_") for c in df_import.columns]
        required = {"ticker", "name"}
        if not required.issubset(df_import.columns):
            st.error(f"CSV must contain columns: {required}. Found: {list(df_import.columns)}")
        else:
            st.dataframe(df_import.head(10), use_container_width=True)
            st.write(f"{len(df_import)} rows total.")
            if st.button("Import companies"):
                added = 0
                skipped = 0
                with Session() as session:
                    for _, row in df_import.iterrows():
                        ticker = str(row["ticker"]).strip()
                        name = str(row.get("name", "")).strip()
                        if not ticker or not name:
                            skipped += 1
                            continue
                        existing = session.get(Company, ticker)
                        if existing:
                            skipped += 1
                            continue
                        session.add(Company(
                            ticker=ticker,
                            name=name,
                            sector=str(row.get("sector", "")).strip() or None,
                            sub_sector=str(row.get("sub_sector", "")).strip() or None,
                            isin=str(row.get("isin", "")).strip() or None,
                            market=str(row.get("market", "")).strip() or None,
                        ))
                        added += 1
                    session.commit()
                st.success(f"Imported {added} companies. {skipped} skipped (already exist or missing data).")
                st.rerun()

    st.subheader("Enrich with live prices (yfinance)")
    if st.button("Fetch prices for all companies"):
        with Session() as session:
            companies = session.query(Company).all()
            tickers = [c.ticker for c in companies]

        progress = st.progress(0)
        status = st.empty()
        total = len(tickers)
        enriched = 0
        with Session() as session:
            for i, ticker in enumerate(tickers):
                progress.progress((i + 1) / total)
                status.text(f"Fetching {ticker}...")
                close, market_cap = _fetch_yfinance(ticker)
                if close or market_cap:
                    today = pd.Timestamp.today().strftime("%Y-%m-%d")
                    existing = (
                        session.query(SharePrice)
                        .filter_by(ticker=ticker, price_date=today)
                        .first()
                    )
                    if existing:
                        existing.close = close
                        existing.market_cap = market_cap
                    else:
                        session.add(SharePrice(
                            ticker=ticker,
                            price_date=today,
                            close=close,
                            market_cap=market_cap,
                        ))
                    enriched += 1
                time.sleep(0.1)
            session.commit()
        st.success(f"Enriched {enriched} of {total} companies.")
        st.rerun()

with tab_screen:
    st.subheader("Filter universe")

    with Session() as session:
        all_companies = (
            session.query(Company)
            .order_by(Company.ticker)
            .all()
        )
        all_tickers = [c.ticker for c in all_companies]
        latest_prices = {}
        for c in all_companies:
            sp = (
                session.query(SharePrice)
                .filter_by(ticker=c.ticker)
                .order_by(SharePrice.price_date.desc())
                .first()
            )
            latest_prices[c.ticker] = sp

    sectors = sorted(set(c.sector for c in all_companies if c.sector))
    markets = sorted(set(c.market for c in all_companies if c.market))

    f1, f2, f3, f4 = st.columns(4)
    sel_sectors = f1.multiselect("Sector", sectors)
    sel_markets = f2.multiselect("Market", markets)
    show_blacklisted = f3.checkbox("Show blacklisted", value=False)
    show_portfolio_only = f4.checkbox("Portfolio only", value=False)

    f5, f6 = st.columns(2)
    min_mcap = f5.number_input("Min market cap (£m)", value=0, step=50)
    max_mcap = f6.number_input("Max market cap (£m)", value=0, step=500,
                               help="0 = no limit")

    rows = []
    for c in all_companies:
        if not show_blacklisted and c.blacklisted:
            continue
        if show_portfolio_only and not c.in_portfolio:
            continue
        if sel_sectors and c.sector not in sel_sectors:
            continue
        if sel_markets and c.market not in sel_markets:
            continue
        sp = latest_prices.get(c.ticker)
        mcap_m = (sp.market_cap / 1e6) if sp and sp.market_cap else None
        if min_mcap > 0 and (mcap_m is None or mcap_m < min_mcap):
            continue
        if max_mcap > 0 and mcap_m is not None and mcap_m > max_mcap:
            continue

        rows.append({
            "Ticker": c.ticker,
            "Name": c.name,
            "Sector": c.sector or "—",
            "Market": c.market or "—",
            "Price": f"{sp.close:.2f}" if sp and sp.close else "—",
            "Mkt Cap (£m)": f"{mcap_m:.0f}" if mcap_m else "—",
            "Watchlist": "✓" if c.in_watchlist else "",
            "Portfolio": "✓" if c.in_portfolio else "",
            "Blacklisted": "✗" if c.blacklisted else "",
        })

    st.write(f"**{len(rows)} companies**")
    if rows:
        df_screen = pd.DataFrame(rows)
        st.dataframe(df_screen, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Blacklist / unblacklist")
    toggle_ticker = st.selectbox("Ticker", [""] + [c.ticker for c in all_companies])
    c1, c2 = st.columns(2)
    if c1.button("Blacklist") and toggle_ticker:
        with Session() as session:
            c = session.get(Company, toggle_ticker)
            if c:
                c.blacklisted = True
                session.commit()
        st.rerun()
    if c2.button("Un-blacklist") and toggle_ticker:
        with Session() as session:
            c = session.get(Company, toggle_ticker)
            if c:
                c.blacklisted = False
                session.commit()
        st.rerun()
