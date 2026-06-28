import streamlit as st
import pandas as pd

from db.database import get_engine, get_session_factory
from db.models import Company, CompanyNote, FinancialPeriod, FinancialLineItem, SharePrice
from pages.ui_helpers import form_company_selectbox

st.title("Watchlist")

engine = get_engine()
Session = get_session_factory(engine)


class _Co:
    __slots__ = ("ticker", "name")
    def __init__(self, t, n): self.ticker, self.name = t, n


def _company_summary_row(session, company):
    periods = (
        session.query(FinancialPeriod)
        .filter_by(ticker=company.ticker)
        .order_by(FinancialPeriod.period_end_date.desc())
        .limit(2)
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

    rev = latest_li.get("revenue")
    op = latest_li.get("operating_profit")
    margin = f"{op / rev * 100:.1f}%" if rev and op and rev != 0 else "—"

    return {
        "Ticker": company.ticker,
        "Name": company.name,
        "Sector": company.sector or "—",
        "Latest Price": f"{latest_price.close:.2f}p" if latest_price and latest_price.close else "—",
        "Market Cap": f"£{latest_price.market_cap / 1e6:.0f}m" if latest_price and latest_price.market_cap else "—",
        "Op Margin": margin,
        "Revenue": f"{rev / 1e6:.1f}m" if rev else "—",
    }


with Session() as session:
    watchlist = (
        session.query(Company)
        .filter_by(in_watchlist=True, blacklisted=False)
        .order_by(Company.ticker)
        .all()
    )
    _all = session.query(Company).order_by(Company.ticker).all()
    all_companies = [_Co(c.ticker, c.name or c.ticker) for c in _all]
    watchlist_tickers = [c.ticker for c in watchlist]

col_left, col_right = st.columns([3, 1])

with col_right:
    st.subheader("Add to watchlist")
    with st.form("add_watchlist"):
        add_ticker = form_company_selectbox(all_companies, key="wl_add", label="Company", include_blank=True)
        if st.form_submit_button("Add") and add_ticker:
            with Session() as session:
                c = session.get(Company, add_ticker)
                if c:
                    c.in_watchlist = True
                    session.commit()
            st.rerun()

with col_left:
    if not watchlist:
        st.info("Watchlist is empty. Add companies using the panel on the right.")
    else:
        with Session() as session:
            rows = [_company_summary_row(session, c) for c in watchlist]
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)

        wl_cos = [_Co(c.ticker, c.name or c.ticker) for c in watchlist]
        wl_options = [""] + [f"{c.ticker} — {c.name}" for c in sorted(wl_cos, key=lambda x: x.name)]
        remove_sel = st.selectbox("Remove from watchlist", wl_options, key="wl_remove")
        remove_ticker = remove_sel.split(" — ")[0] if remove_sel else ""
        if st.button("Remove") and remove_ticker:
            with Session() as session:
                c = session.get(Company, remove_ticker)
                if c:
                    c.in_watchlist = False
                    session.commit()
            st.rerun()

st.divider()
st.subheader("Company Notes")
_note_opts = [""] + [f"{c.ticker} — {c.name or c.ticker}" for c in sorted(watchlist, key=lambda x: x.name or x.ticker)]
_note_sel = st.selectbox("Company", _note_opts, key="note_sel")
note_ticker = _note_sel.split(" — ")[0] if _note_sel else ""
if note_ticker:
    with st.form("add_note_wl"):
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
