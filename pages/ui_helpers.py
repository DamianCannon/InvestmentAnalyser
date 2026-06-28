import streamlit as st


def linked_company_selector(companies, key: str, label: str = "Company") -> str:
    """Two linked dropdowns — ticker (sorted A-Z) and name (sorted A-Z).
    Selecting either updates the other. Returns the selected ticker.
    Must be used outside st.form()."""
    if not companies:
        return ""

    by_ticker = sorted(companies, key=lambda c: c.ticker)
    by_name = sorted(companies, key=lambda c: c.name)

    tickers = [c.ticker for c in by_ticker]
    names = [c.name for c in by_name]
    ticker_to_name = {c.ticker: c.name for c in companies}
    name_to_ticker = {c.name: c.ticker for c in companies}

    t_key = f"{key}__ticker"
    n_key = f"{key}__name"

    if t_key not in st.session_state:
        st.session_state[t_key] = tickers[0]
    if n_key not in st.session_state:
        st.session_state[n_key] = ticker_to_name.get(tickers[0], names[0])

    def _on_ticker():
        st.session_state[n_key] = ticker_to_name.get(st.session_state[t_key], "")

    def _on_name():
        st.session_state[t_key] = name_to_ticker.get(st.session_state[n_key], "")

    col_t, col_n = st.columns(2)
    col_t.selectbox(f"{label} — ticker", tickers, key=t_key, on_change=_on_ticker)
    col_n.selectbox(f"{label} — name", names, key=n_key, on_change=_on_name)

    return st.session_state[t_key]


def form_company_selectbox(companies, key: str, label: str = "Company", include_blank: bool = False) -> str:
    """Single 'TICKER — Name' dropdown sorted by name, safe to use inside st.form().
    Returns the selected ticker string."""
    by_name = sorted(companies, key=lambda c: c.name)
    options = [f"{c.ticker} — {c.name}" for c in by_name]
    if include_blank:
        options = [""] + options
    selected = st.selectbox(label, options, key=key)
    if not selected:
        return ""
    return selected.split(" — ")[0]
