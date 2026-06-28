import json
from datetime import datetime

import pandas as pd
import streamlit as st
import yfinance as yf

from db.database import get_engine, get_session_factory
from db.models import (
    Company, Document, DocumentType, ExtractedTable,
    FinancialLineItem, FinancialPeriod, LineType, PeriodType,
    ReviewStatus, TradingUpdateAnalysis, GuidanceDirection,
)
from ingestion import ai_extractor, mapping_store
from ingestion.html_parser import fetch_and_parse
from ingestion.pdf_parser import extract_tables, filter_junk_tables, render_page
from pages.ui_helpers import linked_company_selector, form_company_selectbox

st.title("Ingest Documents")

engine = get_engine()
Session = get_session_factory(engine)

LINE_TYPES = [lt.value for lt in LineType]
DOC_TYPES = [dt.value for dt in DocumentType]

with Session() as session:
    _all_cos = session.query(Company).order_by(Company.ticker).all()
    tickers = [c.ticker for c in _all_cos]

    class _Co:
        __slots__ = ("ticker", "name")
        def __init__(self, t, n): self.ticker, self.name = t, n

    companies = [_Co(c.ticker, c.name or c.ticker) for c in _all_cos]

if not tickers:
    st.warning("No companies found. Add a company ticker first.")
    with st.form("add_company_quick"):
        st.write("**Quick add company**")
        c1, c2, c3 = st.columns(3)
        new_ticker = c1.text_input("Ticker (e.g. SMWH.L)").strip().upper()
        new_name = c2.text_input("Company name")
        new_type = c3.selectbox("Type", ["equity", "investment_trust"])
        if st.form_submit_button("Add") and new_ticker and new_name:
            from db.models import CompanyType
            with Session() as session:
                if not session.get(Company, new_ticker):
                    session.add(Company(
                        ticker=new_ticker,
                        name=new_name,
                        company_type=CompanyType(new_type),
                    ))
                    session.commit()
            st.rerun()
    st.stop()

tab_pdf, tab_html, tab_add_company = st.tabs(["PDF Upload", "URL / HTML", "Add Company"])

with tab_add_company:
    st.subheader("Search to add")
    st.caption("Type a company name or ticker — results are filtered to LSE-listed securities.")
    search_q = st.text_input("Search", placeholder="e.g. Tatton, Smith & Nephew, TAM.L", key="yf_search_q")
    if st.button("Search", key="yf_search_btn") and search_q:
        with st.spinner("Searching yfinance…"):
            try:
                results = yf.Search(search_q, news_count=0).quotes
                lse_hits = [
                    q for q in results
                    if str(q.get("symbol", "")).endswith(".L")
                    or q.get("exchange") in ("LSE", "AIM", "IOB")
                ]
                st.session_state["yf_results"] = lse_hits
            except Exception as e:
                st.error(f"Search failed: {e}")
                st.session_state["yf_results"] = []

    for q in st.session_state.get("yf_results", []):
        symbol = q.get("symbol", "")
        name = q.get("shortname") or q.get("longname", symbol)
        exch = q.get("exchDisp", q.get("exchange", ""))
        r1, r2, r3 = st.columns([4, 2, 1])
        r1.write(f"**{symbol}** — {name}")
        r2.caption(exch)
        if r3.button("Add", key=f"yf_add_{symbol}"):
            with Session() as session:
                if not session.get(Company, symbol):
                    session.add(Company(
                        ticker=symbol,
                        name=name,
                        company_type=CompanyType.equity,
                        is_listed=True,
                    ))
                    session.commit()
            st.success(f"Added {symbol} — {name}")
            st.session_state["yf_results"] = []
            st.rerun()

    st.divider()
    st.subheader("Manual add / update")
    with st.form("add_company"):
        c1, c2 = st.columns(2)
        new_ticker = c1.text_input("Ticker (e.g. SMWH.L)").strip().upper()
        new_name = c2.text_input("Company name")
        c3, c4, c5 = st.columns(3)
        new_sector = c3.text_input("Sector")
        new_market = c4.text_input("Market (e.g. LSE Main)")
        new_type = c5.selectbox("Type", ["equity", "investment_trust"])
        if st.form_submit_button("Save") and new_ticker and new_name:
            from db.models import CompanyType as _CT
            with Session() as session:
                existing = session.get(Company, new_ticker)
                if existing:
                    existing.name = new_name
                    existing.sector = new_sector or existing.sector
                    existing.market = new_market or existing.market
                else:
                    session.add(Company(
                        ticker=new_ticker,
                        name=new_name,
                        sector=new_sector or None,
                        market=new_market or None,
                        company_type=_CT(new_type),
                        is_listed=True,
                    ))
                session.commit()
            st.success(f"Saved {new_ticker}")
            st.rerun()


def _safe_dataframe(headers: list, rows: list) -> pd.DataFrame:
    """Build a DataFrame, deduplicating column names to avoid Arrow serialisation errors."""
    if rows and headers and len(headers) == len(rows[0]):
        seen: dict[str, int] = {}
        deduped = []
        for h in headers:
            if h in seen:
                seen[h] += 1
                deduped.append(f"{h}.{seen[h]}")
            else:
                seen[h] = 0
                deduped.append(h)
        return pd.DataFrame(rows, columns=deduped)
    return pd.DataFrame(rows)


def _run_review_ui(tables, doc_type, ticker, document_id, session_factory, file_bytes=None):
    if not tables:
        st.warning("No tables extracted from document.")
        return

    st.write(f"**{len(tables)} table(s) selected.** Review AI proposals and confirm.")

    with session_factory() as session:
        existing_mapping = mapping_store.get_mapping(session, ticker, doc_type)

    for tbl in tables:
        idx = tbl["index"]
        page_num_0 = tbl.get("page", 1) - 1
        st.markdown(f"---\n#### Table {idx + 1} (page {tbl.get('page', '?')})")

        mapping_key = f"mapping_{document_id}_{idx}"
        if mapping_key not in st.session_state:
            with st.spinner("Asking AI for mapping…"):
                if file_bytes:
                    img = render_page(file_bytes, page_num_0)
                    proposal = ai_extractor.propose_mapping_from_image(img, ticker, doc_type) if img else None
                    if proposal and "extracted_rows" in proposal:
                        period_cols = proposal["period_columns"]
                        tbl["headers"] = [""] + period_cols
                        tbl["rows"] = [
                            [r["label"]] + [str(r["values"].get(p, "")) for p in period_cols]
                            for r in proposal["extracted_rows"]
                        ]
                else:
                    proposal = ai_extractor.propose_mapping(tbl, ticker, doc_type)
                st.session_state[mapping_key] = proposal or {}

        proposal = st.session_state[mapping_key]

        headers = tbl.get("headers", [])
        rows = tbl.get("rows", [])

        col_raw, col_mapped = st.columns(2)
        with col_raw:
            if file_bytes:
                img = render_page(file_bytes, page_num_0)
                if img:
                    st.image(img, caption=f"Page {page_num_0 + 1}", use_container_width=True)
                else:
                    st.dataframe(_safe_dataframe(headers, rows), use_container_width=True)
            else:
                st.write("**Raw extracted data**")
                st.dataframe(_safe_dataframe(headers, rows), use_container_width=True)

        with col_mapped:
            st.write("**Mapping proposal**")
            if not proposal:
                st.info("AI did not identify financial data in this table.")
                continue

            row_mappings = proposal.get("row_mappings", {})
            period_columns = proposal.get("period_columns", headers)

            st.write(f"Period columns: `{', '.join(str(p) for p in period_columns)}`")

            edited_mappings = {}
            for row_label, line_type in row_mappings.items():
                new_type = st.selectbox(
                    f"`{row_label}`",
                    ["— skip —"] + LINE_TYPES,
                    index=(LINE_TYPES.index(line_type) + 1) if line_type in LINE_TYPES else 0,
                    key=f"lt_{document_id}_{idx}_{row_label}",
                )
                if new_type != "— skip —":
                    edited_mappings[row_label] = new_type

        confirm_key = f"confirm_{document_id}_{idx}"
        if st.button(f"Confirm table {idx + 1}", key=confirm_key):
            _persist_table(
                tables=[tbl],
                row_mappings=edited_mappings,
                period_columns=period_columns,
                ticker=ticker,
                doc_type=doc_type,
                document_id=document_id,
                table_idx=idx,
                session_factory=session_factory,
            )
            st.success(f"Table {idx + 1} saved.")


def _persist_table(tables, row_mappings, period_columns, ticker, doc_type, document_id, table_idx, session_factory):
    tbl = next((t for t in tables if t["index"] == table_idx), None)
    if not tbl:
        return
    rows = tbl.get("rows", [])
    headers = tbl.get("headers", [])

    with session_factory() as session:
        mapping_store.save_mapping(session, ticker, doc_type, {
            "row_mappings": row_mappings,
            "period_columns": period_columns,
        })

        for row in rows:
            row_label = row[0] if row else ""
            if row_label not in row_mappings:
                continue
            line_type_str = row_mappings[row_label]
            line_type = LineType(line_type_str)

            for col_idx, col_header in enumerate(headers[1:], start=1):
                if col_header not in period_columns:
                    continue
                try:
                    raw_val = row[col_idx].replace(",", "").replace("(", "-").replace(")", "").strip()
                    if not raw_val or raw_val in ("-", "—", "n/a", "N/A"):
                        continue
                    value = float(raw_val)
                except (ValueError, IndexError):
                    continue

                period_date = col_header
                period_type = PeriodType.interim if "H1" in period_date or "H2" in period_date else PeriodType.annual

                existing_period = (
                    session.query(FinancialPeriod)
                    .filter_by(ticker=ticker, period_end_date=period_date, period_type=period_type)
                    .first()
                )
                if not existing_period:
                    existing_period = FinancialPeriod(
                        ticker=ticker,
                        period_end_date=period_date,
                        period_type=period_type,
                        source_document_id=document_id,
                    )
                    session.add(existing_period)
                    session.flush()

                existing_item = (
                    session.query(FinancialLineItem)
                    .filter_by(period_id=existing_period.id, line_type=line_type, is_canonical=True)
                    .first()
                )
                if existing_item:
                    existing_item.value = value
                else:
                    session.add(FinancialLineItem(
                        period_id=existing_period.id,
                        line_type=line_type,
                        value=value,
                        is_canonical=True,
                    ))

        doc = session.get(Document, document_id)
        if doc:
            doc.processed_at = datetime.utcnow()
        session.commit()


with tab_pdf:
    st.subheader("Upload PDF Report")
    pdf_ticker = linked_company_selector(companies, key="pdf_co")
    c1, c2 = st.columns(2)
    pdf_doc_type = c1.selectbox("Document type", DOC_TYPES, key="pdf_doc_type")
    _pdf_date = c2.date_input("Period end date", value=None, format="DD/MM/YYYY", key="pdf_period_dt")
    pdf_period = _pdf_date.strftime("%Y-%m-%d") if _pdf_date else ""
    uploaded_file = st.file_uploader("PDF file", type=["pdf"])

    if uploaded_file and pdf_ticker and pdf_period:
        if st.button("Extract tables"):
            with st.spinner("Extracting tables from PDF..."):
                file_bytes = uploaded_file.read()
                tables = extract_tables(file_bytes, uploaded_file.name)
                st.session_state["pdf_tables"] = tables
                st.session_state["pdf_file_bytes"] = file_bytes
                st.session_state["pdf_doc_id"] = None
                st.session_state.pop("pdf_tables_to_analyze", None)

            if tables:
                with Session() as session:
                    doc = Document(
                        ticker=pdf_ticker,
                        document_type=DocumentType(pdf_doc_type),
                        file_path=uploaded_file.name,
                        period_end_date=pdf_period,
                        uploaded_at=datetime.utcnow(),
                    )
                    session.add(doc)
                    session.commit()
                    st.session_state["pdf_doc_id"] = doc.id
                st.success(f"Extracted {len(tables)} table(s).")
            else:
                st.error("No tables found in PDF.")

    if st.session_state.get("pdf_tables") and st.session_state.get("pdf_doc_id"):
        if not st.session_state.get("pdf_tables_to_analyze"):
            all_tables = st.session_state["pdf_tables"]
            auto_financial = {t["index"] for t in filter_junk_tables(all_tables)}
            st.write(
                f"**{len(all_tables)} tables found.** "
                f"{len(auto_financial)} auto-detected as financial (pre-selected). "
                f"Click 👁 to preview, adjust checkboxes, then **Analyse selected**."
            )

            sel_col, prev_col = st.columns([4, 6])

            with sel_col:
                ba, bn = st.columns(2)
                if ba.button("Select all"):
                    for t in all_tables:
                        st.session_state[f"pdf_sel_{t['index']}"] = True
                    st.rerun()
                if bn.button("Deselect all"):
                    for t in all_tables:
                        st.session_state[f"pdf_sel_{t['index']}"] = False
                    st.rerun()

                for tbl in all_tables:
                    idx = tbl["index"]
                    page = tbl.get("page", "?")
                    headers = tbl.get("headers", [])
                    rows = tbl.get("rows", [])
                    label = f"Table {idx + 1} (p.{page}) — {len(rows)}r × {len(headers)}c"
                    chk_c, eye_c = st.columns([5, 1])
                    with chk_c:
                        st.checkbox(label, value=(idx in auto_financial), key=f"pdf_sel_{idx}")
                    with eye_c:
                        if st.button("👁", key=f"prev_btn_{idx}", help="Preview this table"):
                            st.session_state["pdf_preview_idx"] = idx
                            st.rerun()

                st.divider()
                if st.button("Analyse selected tables", type="primary"):
                    selected = [t for t in all_tables if st.session_state.get(f"pdf_sel_{t['index']}", False)]
                    if selected:
                        st.session_state["pdf_tables_to_analyze"] = selected
                        st.rerun()
                    else:
                        st.warning("No tables selected.")

            with prev_col:
                pidx = st.session_state.get("pdf_preview_idx")
                if pidx is not None:
                    ptbl = next((t for t in all_tables if t["index"] == pidx), None)
                    if ptbl:
                        st.write(f"**Table {pidx + 1} — page {ptbl.get('page', '?')}**")
                        fb = st.session_state.get("pdf_file_bytes")
                        if fb:
                            img = render_page(fb, ptbl.get("page", 1) - 1)
                            if img:
                                st.image(img, use_container_width=True)
                            else:
                                st.dataframe(_safe_dataframe(ptbl.get("headers", []), ptbl.get("rows", [])), use_container_width=True)
                        else:
                            st.dataframe(_safe_dataframe(ptbl.get("headers", []), ptbl.get("rows", [])), use_container_width=True)
                else:
                    st.info("Click 👁 next to a table to preview it here.")
        else:
            if st.button("← Back to table selection"):
                st.session_state.pop("pdf_tables_to_analyze", None)
                st.rerun()
            _run_review_ui(
                st.session_state["pdf_tables_to_analyze"],
                pdf_doc_type,
                pdf_ticker,
                st.session_state["pdf_doc_id"],
                Session,
                file_bytes=st.session_state.get("pdf_file_bytes"),
            )

with tab_html:
    st.subheader("Fetch from URL")
    html_ticker = linked_company_selector(companies, key="html_co")
    c1, c2 = st.columns(2)
    html_doc_type = c1.selectbox("Document type", DOC_TYPES, key="html_doc_type")
    _html_date = c2.date_input("Period end date", value=None, format="DD/MM/YYYY", key="html_period_dt")
    html_period = _html_date.strftime("%Y-%m-%d") if _html_date else ""
    url = st.text_input("URL (Investegate / LSE RNS / company IR page)")

    if url and html_ticker:
        if st.button("Fetch and parse"):
            with st.spinner(f"Fetching {url}..."):
                try:
                    tables, raw_text = fetch_and_parse(url)
                    st.session_state["html_tables"] = tables
                    st.session_state["html_raw_text"] = raw_text
                    st.session_state["html_doc_id"] = None

                    with Session() as session:
                        doc = Document(
                            ticker=html_ticker,
                            document_type=DocumentType(html_doc_type),
                            source_url=url,
                            period_end_date=html_period or None,
                            raw_text=raw_text,
                            uploaded_at=datetime.utcnow(),
                        )
                        session.add(doc)
                        session.commit()
                        st.session_state["html_doc_id"] = doc.id

                    st.success(f"Fetched. {len(tables)} table(s), {len(raw_text)} chars of text.")
                except Exception as e:
                    st.error(f"Failed to fetch: {e}")

    if st.session_state.get("html_tables") is not None and st.session_state.get("html_doc_id"):
        if html_doc_type == "trading_update":
            st.subheader("Trading Update Analysis")
            if st.button("Run AI guidance extraction"):
                with st.spinner("Analysing with AI..."):
                    raw_text = st.session_state.get("html_raw_text", "")
                    analyses = ai_extractor.analyze_trading_update(raw_text)
                    with Session() as session:
                        doc_id = st.session_state["html_doc_id"]
                        for item in analyses:
                            direction = item.get("direction", "unclear")
                            try:
                                dir_enum = GuidanceDirection(direction)
                            except ValueError:
                                dir_enum = GuidanceDirection.unclear
                            session.add(TradingUpdateAnalysis(
                                document_id=doc_id,
                                metric_type=item.get("metric"),
                                guidance_direction=dir_enum,
                                guidance_text=item.get("quote"),
                                extrapolated_value=item.get("implied_value"),
                                flagged_discrepancy=False,
                            ))
                        session.commit()
                    st.session_state["trading_analyses"] = analyses
                    st.success(f"Extracted {len(analyses)} guidance items.")

            if st.session_state.get("trading_analyses"):
                df_ta = pd.DataFrame(st.session_state["trading_analyses"])
                st.dataframe(df_ta, use_container_width=True)
        else:
            _run_review_ui(
                st.session_state["html_tables"],
                html_doc_type,
                html_ticker,
                st.session_state["html_doc_id"],
                Session,
            )
