import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="Investment Analyser",
    page_icon="📈",
    layout="wide",
)

from db.database import get_engine, get_session_factory, init_db


@st.cache_resource
def setup_db():
    engine = get_engine()
    init_db(engine)
    return engine


engine = setup_db()
SessionLocal = get_session_factory(engine)

pages = {
    "Portfolio": [
        st.Page("pages/company.py", title="Company", icon="🏢"),
        st.Page("pages/portfolio.py", title="Portfolio", icon="💼"),
        st.Page("pages/watchlist.py", title="Watchlist", icon="👁"),
    ],
    "Discovery": [
        st.Page("pages/universe.py", title="Universe", icon="🌐"),
    ],
    "Data": [
        st.Page("pages/ingest.py", title="Ingest", icon="📥"),
    ],
}

pg = st.navigation(pages)
pg.run()
