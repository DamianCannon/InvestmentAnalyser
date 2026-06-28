import io
import time

import pandas as pd
import requests
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


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Map LSE instruments file columns (Tidm, Instrument name, …) to standard names."""
    df.columns = [c.strip() for c in df.columns]
    col_lower = {c: c.lower().replace(" ", "_") for c in df.columns}

    rename = {}
    for orig, lower in col_lower.items():
        if lower == "tidm":
            rename[orig] = "ticker"
        elif lower in ("instrument_name", "company_name", "name"):
            rename[orig] = "name"
        elif lower == "isin":
            rename[orig] = "isin"
        elif "supersector" in lower or "super_sector" in lower:
            rename[orig] = "sub_sector"
        elif "sector" in lower and "sub" not in lower and "super" not in lower:
            rename[orig] = "sector"
        elif "market" in lower and "cap" not in lower:
            rename[orig] = "market"

    df = df.rename(columns=rename)

    # LSE Tidm tickers have no suffix — append .L for yfinance compatibility
    if "ticker" in df.columns and "name" in df.columns:
        df["ticker"] = df["ticker"].astype(str).str.strip()
        df["ticker"] = df["ticker"].apply(
            lambda t: f"{t}.L" if t and "." not in t and t not in ("", "nan") else t
        )

    return df


def _import_dataframe(df: pd.DataFrame, mark_others_delisted: bool) -> tuple[int, int]:
    """Upsert companies from df. Returns (added, updated)."""
    added = updated = 0
    imported_tickers = set()

    with Session() as session:
        for _, row in df.iterrows():
            ticker = str(row.get("ticker", "")).strip()
            name = str(row.get("name", "")).strip()
            if not ticker or not name or ticker == "nan":
                continue
            imported_tickers.add(ticker)
            existing = session.get(Company, ticker)
            if existing:
                existing.name = name
                existing.sector = str(row.get("sector", "")).strip() or existing.sector
                existing.sub_sector = str(row.get("sub_sector", "")).strip() or existing.sub_sector
                existing.isin = str(row.get("isin", "")).strip() or existing.isin
                existing.market = str(row.get("market", "")).strip() or existing.market
                existing.is_listed = True
                updated += 1
            else:
                session.add(Company(
                    ticker=ticker,
                    name=name,
                    sector=str(row.get("sector", "")).strip() or None,
                    sub_sector=str(row.get("sub_sector", "")).strip() or None,
                    isin=str(row.get("isin", "")).strip() or None,
                    market=str(row.get("market", "")).strip() or None,
                    is_listed=True,
                ))
                added += 1

        if mark_others_delisted and imported_tickers:
            for co in session.query(Company).all():
                if co.ticker not in imported_tickers:
                    co.is_listed = False

        session.commit()

    return added, updated


@st.cache_data(ttl=86400)
def _fetch_ftse_wikipedia(url: str, table_index: int) -> pd.DataFrame:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; InvestmentAnalyser/1.0)"}
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    return tables[table_index]


def _scrape_lse_constituents(base_url: str, market_label: str, status_widget) -> pd.DataFrame:
    """Paginate an LSE constituent table using ?page=N URL parameter."""
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    all_rows = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        ).new_page()

        page_num = 1
        while True:
            url = f"{base_url}?page={page_num}"
            status_widget.text(f"Fetching page {page_num} — {len(all_rows)} companies so far…")

            try:
                page.goto(url, wait_until="networkidle", timeout=30_000)
                page.wait_for_selector("table tbody tr", timeout=10_000)
            except PWTimeout:
                break  # no table appeared — past the last page

            page_rows = page.evaluate("""
                Array.from(document.querySelectorAll('table tbody tr')).map(tr =>
                    Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim())
                ).filter(r => r.length >= 2)
            """)

            if not page_rows:
                break  # empty table — done

            added_this_page = 0
            for cells in page_rows:
                name = cells[0] if len(cells) > 0 else ""
                tidm = cells[1] if len(cells) > 1 else ""
                isin  = cells[2] if len(cells) > 2 else ""
                if not name or not tidm or tidm.upper() in ("TIDM", ""):
                    continue
                all_rows.append({
                    "ticker": f"{tidm}.L" if "." not in tidm else tidm,
                    "name": name,
                    "isin": isin,
                    "market": market_label,
                })
                added_this_page += 1

            if added_this_page == 0:
                break  # page loaded but had no usable rows — done

            page_num += 1

        browser.close()

    return pd.DataFrame(all_rows) if all_rows else pd.DataFrame(
        columns=["ticker", "name", "isin", "market"]
    )


tab_import, tab_fetch, tab_screen = st.tabs(["CSV / Excel Upload", "Auto-fetch", "Screen"])

# ── CSV / Excel Upload ───────────────────────────────────────────────────────
with tab_import:
    st.subheader("Import instruments file")
    st.info(
        "**LSE instruments file location:** "
        "londonstockexchange.com → Live Markets → Market Data & Reports → Shares Data → "
        "Company Data (Main Market) or AIM tab. Click the download/export button. "
        "The file uses **Tidm** for ticker and **Instrument name** for company name — "
        "the importer maps these automatically and appends `.L` to tickers."
    )
    uploaded = st.file_uploader("CSV or Excel file", type=["csv", "xls", "xlsx"])
    mark_delisted = st.checkbox(
        "Mark companies not in this file as delisted (keeps them in watchlist/portfolio but hides from universe)",
        value=False,
    )
    if uploaded:
        raw = uploaded.read()
        try:
            if uploaded.name.endswith((".xls", ".xlsx")):
                df_import = pd.read_excel(io.BytesIO(raw))
            else:
                df_import = pd.read_csv(io.BytesIO(raw))
        except Exception as e:
            st.error(f"Could not read file: {e}")
            df_import = None

        if df_import is not None:
            df_import = _normalise_columns(df_import)
            if not {"ticker", "name"}.issubset(df_import.columns):
                st.error(
                    f"Could not find ticker and name columns. Detected columns: {list(df_import.columns)}. "
                    "Expected at minimum: ticker/Tidm and name/Instrument name."
                )
            else:
                st.dataframe(df_import[["ticker", "name"] + [c for c in df_import.columns if c not in ("ticker", "name")]].head(10), use_container_width=True)
                st.write(f"{len(df_import)} rows detected.")
                if st.button("Import"):
                    added, updated = _import_dataframe(df_import, mark_delisted)
                    st.success(f"Done — {added} new, {updated} updated.")
                    st.rerun()

# ── Auto-fetch ───────────────────────────────────────────────────────────────
with tab_fetch:
    st.subheader("Auto-fetch index constituents")

    col_f1, col_f2 = st.columns(2)

    with col_f1:
        st.write("**FTSE 100** (~100 large-caps, Main Market)")
        if st.button("Fetch FTSE 100"):
            with st.spinner("Fetching from Wikipedia…"):
                try:
                    df_raw = _fetch_ftse_wikipedia(
                        "https://en.wikipedia.org/wiki/FTSE_100_Index", 4
                    )
                    df_raw.columns = [c.strip() for c in df_raw.columns]
                    ticker_col = next((c for c in df_raw.columns if "ticker" in c.lower() or "tidm" in c.lower() or "epic" in c.lower()), None)
                    name_col = next((c for c in df_raw.columns if "company" in c.lower() or "name" in c.lower()), None)
                    if ticker_col and name_col:
                        df_ftse = pd.DataFrame({
                            "ticker": df_raw[ticker_col].astype(str).str.strip().apply(lambda t: f"{t}.L" if "." not in t else t),
                            "name": df_raw[name_col].astype(str).str.strip(),
                            "market": "Main Market",
                        })
                        added, updated = _import_dataframe(df_ftse, mark_others_delisted=False)
                        st.success(f"FTSE 100 — {added} new, {updated} updated.")
                        _fetch_ftse_wikipedia.clear()
                    else:
                        st.error(f"Couldn't identify ticker/name columns. Found: {list(df_raw.columns)}")
                except Exception as e:
                    st.error(f"Fetch failed: {e}")

    with col_f2:
        st.write("**FTSE 250** (~250 mid-caps, Main Market)")
        if st.button("Fetch FTSE 250"):
            with st.spinner("Fetching from Wikipedia…"):
                try:
                    df_raw = _fetch_ftse_wikipedia(
                        "https://en.wikipedia.org/wiki/FTSE_250_Index", 3
                    )
                    df_raw.columns = [c.strip() for c in df_raw.columns]
                    ticker_col = next((c for c in df_raw.columns if "ticker" in c.lower() or "tidm" in c.lower() or "epic" in c.lower()), None)
                    name_col = next((c for c in df_raw.columns if "company" in c.lower() or "name" in c.lower()), None)
                    if ticker_col and name_col:
                        df_ftse = pd.DataFrame({
                            "ticker": df_raw[ticker_col].astype(str).str.strip().apply(lambda t: f"{t}.L" if "." not in t else t),
                            "name": df_raw[name_col].astype(str).str.strip(),
                            "market": "Main Market",
                        })
                        added, updated = _import_dataframe(df_ftse, mark_others_delisted=False)
                        st.success(f"FTSE 250 — {added} new, {updated} updated.")
                        _fetch_ftse_wikipedia.clear()
                    else:
                        st.error(f"Couldn't identify ticker/name columns. Found: {list(df_raw.columns)}")
                except Exception as e:
                    st.error(f"Fetch failed: {e}")

    st.divider()
    st.subheader("LSE live pages (Playwright)")
    st.caption(
        "These fetch directly from londonstockexchange.com by rendering the page in a headless browser. "
        "Takes 1–3 minutes depending on constituent count."
    )

    col_aim, col_all = st.columns(2)

    with col_aim:
        st.write("**FTSE AIM All-Share** (~700 AIM-listed stocks)")
        if st.button("Fetch AIM All-Share"):
            status = st.empty()
            try:
                df_aim = _scrape_lse_constituents(
                    "https://www.londonstockexchange.com/indices/ftse-aim-all-share/constituents/table",
                    "AIM",
                    status,
                )
                if df_aim.empty:
                    st.error("No data returned — the page structure may have changed.")
                else:
                    added, updated = _import_dataframe(df_aim, mark_others_delisted=False)
                    status.empty()
                    st.success(f"AIM All-Share — {len(df_aim)} companies fetched, {added} new, {updated} updated.")
            except Exception as e:
                st.error(f"Scrape failed: {e}")

    with col_all:
        st.write("**FTSE All-Share** (~600 Main Market stocks)")
        if st.button("Fetch FTSE All-Share"):
            status = st.empty()
            try:
                df_all = _scrape_lse_constituents(
                    "https://www.londonstockexchange.com/indices/ftse-all-share/constituents/table",
                    "Main Market",
                    status,
                )
                if df_all.empty:
                    st.error("No data returned — the page structure may have changed.")
                else:
                    added, updated = _import_dataframe(df_all, mark_others_delisted=False)
                    status.empty()
                    st.success(f"FTSE All-Share — {len(df_all)} companies fetched, {added} new, {updated} updated.")
            except Exception as e:
                st.error(f"Scrape failed: {e}")

    st.divider()
    st.subheader("Enrich with live prices (yfinance)")
    if st.button("Fetch prices for all companies"):
        with Session() as session:
            all_tickers = [c.ticker for c in session.query(Company).filter_by(is_listed=True).all()]
        progress = st.progress(0)
        status = st.empty()
        enriched = 0
        with Session() as session:
            for i, ticker in enumerate(all_tickers):
                progress.progress((i + 1) / len(all_tickers))
                status.text(f"Fetching {ticker}…")
                close, market_cap = _fetch_yfinance(ticker)
                if close or market_cap:
                    today = pd.Timestamp.today().strftime("%Y-%m-%d")
                    existing = session.query(SharePrice).filter_by(ticker=ticker, price_date=today).first()
                    if existing:
                        existing.close = close
                        existing.market_cap = market_cap
                    else:
                        session.add(SharePrice(ticker=ticker, price_date=today, close=close, market_cap=market_cap))
                    enriched += 1
                time.sleep(0.1)
            session.commit()
        st.success(f"Enriched {enriched} of {len(all_tickers)} companies.")

# ── Screen ───────────────────────────────────────────────────────────────────
with tab_screen:
    st.subheader("Filter universe")

    with Session() as session:
        all_companies = session.query(Company).order_by(Company.name).all()
        latest_prices = {}
        for c in all_companies:
            sp = session.query(SharePrice).filter_by(ticker=c.ticker).order_by(SharePrice.price_date.desc()).first()
            latest_prices[c.ticker] = sp

    sectors = sorted(set(c.sector for c in all_companies if c.sector))
    markets = sorted(set(c.market for c in all_companies if c.market))

    f1, f2, f3, f4, f5 = st.columns(5)
    sel_sectors = f1.multiselect("Sector", sectors)
    sel_markets = f2.multiselect("Market", markets)
    show_blacklisted = f3.checkbox("Show blacklisted", value=False)
    show_delisted = f4.checkbox("Show delisted", value=False)
    show_portfolio_only = f5.checkbox("Portfolio only", value=False)

    fc1, fc2 = st.columns(2)
    min_mcap = fc1.number_input("Min market cap (£m)", value=0, step=50)
    max_mcap = fc2.number_input("Max market cap (£m)", value=0, step=500, help="0 = no limit")

    rows = []
    for c in all_companies:
        if not show_blacklisted and c.blacklisted:
            continue
        if not show_delisted and not c.is_listed:
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
            "Listed": "✓" if c.is_listed else "✗ delisted",
            "Watchlist": "✓" if c.in_watchlist else "",
            "Portfolio": "✓" if c.in_portfolio else "",
        })

    st.write(f"**{len(rows)} companies**")
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Blacklist / unblacklist")
    listed_options = [""] + [f"{c.ticker} — {c.name}" for c in sorted(all_companies, key=lambda x: x.name) if c.is_listed]
    toggle_sel = st.selectbox("Company", listed_options, key="bl_toggle")
    toggle_ticker = toggle_sel.split(" — ")[0] if toggle_sel else ""
    bc1, bc2 = st.columns(2)
    if bc1.button("Blacklist") and toggle_ticker:
        with Session() as session:
            c = session.get(Company, toggle_ticker)
            if c:
                c.blacklisted = True
                session.commit()
        st.rerun()
    if bc2.button("Un-blacklist") and toggle_ticker:
        with Session() as session:
            c = session.get(Company, toggle_ticker)
            if c:
                c.blacklisted = False
                session.commit()
        st.rerun()
