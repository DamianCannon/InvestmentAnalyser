import streamlit as st
import pandas as pd

from db.database import get_engine, get_session_factory
from db.models import Company, CompanyNote, FinancialPeriod, FinancialLineItem, SharePrice

st.title("Portfolio")

engine = get_engine()
Session = get_session_factory(engine)


def _company_summary_row(session, company):
    periods = (
        session.query(FinancialPeriod)
        .filter_by(ticker=company.ticker)
        .order_by(FinancialPeriod.period_end_date.desc())
        .limit(1)
        .all()
    )
    latest_li = {}
    if periods:
        items = session.query(FinancialLineItem).filter_by(
            period_id=periods[0].id, is_canonical=True
        ).all()
        latest_li = {i.line_type.value: i.value for i in items}

    latest_price = (
        session.query(SharePrice)
        .filter_by(ticker=company.ticker)
        .order_by(SharePrice.price_date.desc())
        .first()
    )

    eps = latest_li.get("eps_reported")
    price = latest_price.close if latest_price else None
    pe = f"{price / eps:.1f}x" if price and eps and eps > 0 else "—"
    dps = latest_li.get("dps")
    div_yield = f"{dps / price * 100:.1f}%" if price and dps and price > 0 else "—"

    return {
        "Ticker": company.ticker,
        "Name": company.name,
        "Sector": company.sector or "—",
        "Price": f"{price:.2f}p" if price else "—",
        "Market Cap": f"£{latest_price.market_cap / 1e6:.0f}m" if latest_price and latest_price.market_cap else "—",
        "P/E": pe,
        "Div Yield": div_yield,
        "EPS": f"{eps:.2f}p" if eps else "—",
    }


with Session() as session:
    portfolio = (
        session.query(Company)
        .filter_by(in_portfolio=True, blacklisted=False)
        .order_by(Company.ticker)
        .all()
    )
    all_tickers = [c.ticker for c in session.query(Company).order_by(Company.ticker).all()]

col_left, col_right = st.columns([3, 1])

with col_right:
    st.subheader("Add to portfolio")
    with st.form("add_portfolio"):
        add_ticker = st.selectbox("Ticker", [""] + all_tickers)
        if st.form_submit_button("Add") and add_ticker:
            with Session() as session:
                c = session.get(Company, add_ticker)
                if c:
                    c.in_portfolio = True
                    session.commit()
            st.rerun()

with col_left:
    if not portfolio:
        st.info("Portfolio is empty. Add companies using the panel on the right.")
    else:
        with Session() as session:
            rows = [_company_summary_row(session, c) for c in portfolio]
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)

        remove_ticker = st.selectbox("Remove from portfolio", [""] + [c.ticker for c in portfolio])
        if st.button("Remove") and remove_ticker:
            with Session() as session:
                c = session.get(Company, remove_ticker)
                if c:
                    c.in_portfolio = False
                    session.commit()
            st.rerun()

st.divider()
st.subheader("Investment Notes")
note_ticker = st.selectbox("Company", [""] + [c.ticker for c in portfolio], key="port_note_sel")
if note_ticker:
    with st.form("add_note_port"):
        note_text = st.text_area("Note", height=80)
        if st.form_submit_button("Save"):
            if note_text.strip():
                with Session() as session:
                    session.add(CompanyNote(ticker=note_ticker, note_text=note_text.strip()))
                    session.commit()
                st.rerun()

    with Session() as session:
        notes = (
            session.query(CompanyNote)
            .filter_by(ticker=note_ticker)
            .order_by(CompanyNote.created_at.desc())
            .limit(10)
            .all()
        )
    for n in notes:
        ts = n.created_at.strftime("%Y-%m-%d %H:%M") if n.created_at else ""
        st.markdown(f"**{ts}** — {n.note_text}")
