# Investment Analyser — Project Reference

## Purpose

Personal investment analysis tool for a private long-term UK equity investor (~30 stocks, mix of equities and investment trusts). The goal is to track historical fundamentals from company reports, calculate investment metrics, surface trends over time, and screen the full UK listed universe — without relying entirely on a third-party service like Stockopedia (though the user has a Stockopedia subscription for reference).

The user is not a day trader. Incremental ROCE (quality of new capital investment), cash conversion, and adjusted vs reported EPS divergence are particularly important. The tool should help the user understand how businesses compound over time.

---

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Language | Python | Best library ecosystem for this domain |
| UI | Streamlit (SPA-style multi-page) | Rapid development, no JS required |
| Database | SQLite via SQLAlchemy ORM | Local, zero-infra, plenty for 30–100 companies |
| AI extraction | Claude API (claude-sonnet-4-6) | Best for structured data from messy PDFs |
| PDF rendering | PyMuPDF (`fitz`) | Renders pages as images for Claude vision |
| PDF table detection | pdfplumber | Used only for detection/filtering, not data extraction |
| HTML parsing | BeautifulSoup | For HTML-based announcements |
| Price data | yfinance | On-demand OHLC and market cap fetch |
| Company search | yfinance Search API | Individual ticker/name lookup |
| Charts | Plotly | Interactive, embeds well in Streamlit |
| Web scraping | Playwright (headless Chromium) | LSE is a JS-rendered SPA; requests alone won't work |
| Wikipedia fetch | `requests` + `pd.read_html` | FTSE 100/250 index constituent tables |

---

## Application Structure

```
app.py                  — Entry point; sets up DB, runs migrations, defines nav pages
db/
  models.py             — SQLAlchemy ORM models + all Enums
  database.py           — Engine, session factory, init_db, run_migrations
ingestion/
  pdf_parser.py         — Table detection (pdfplumber), junk filtering, page image rendering (PyMuPDF)
  ai_extractor.py       — Claude API calls: text-based and vision-based table extraction
  html_parser.py        — HTML-based announcement parsing
  mapping_store.py      — Per-company reusable extraction mappings
metrics/
  calculator.py         — All metric calculations (single-period and cross-period growth/CAGR)
pages/
  company.py            — Company detail page (metrics table, charts, trading updates, forecasts, notes)
  ingest.py             — Document upload + AI extraction review workflow
  portfolio.py          — Portfolio management
  watchlist.py          — Watchlist management
  universe.py           — Company universe (CSV upload, FTSE/AIM auto-fetch, screening)
  ui_helpers.py         — Shared input widgets (linked dropdowns, combined selectbox)
```

---

## Database Schema

### Key Models

**Company** — ticker (PK), name, market, sector, sub_sector, isin, company_type (equity|investment_trust), blacklisted, in_watchlist, in_portfolio, is_listed, created_at

**Document** — id, ticker (FK), document_type, source_url, file_path, period_end_date, raw_text, uploaded_at, processed_at

**FinancialPeriod** — id, ticker (FK), period_end_date, period_type (annual|interim), is_audited, source_document_id. Unique constraint on (ticker, period_end_date, period_type).

**FinancialLineItem** — id, period_id (FK), line_type (LineType enum), value, is_adjusted, version, is_canonical

**TableMapping** — per-company reusable extraction config (stored as JSON)

**ForecastEntry** — manually entered consensus forecasts with timestamps (to track drift)

**SharePrice** — date, close, market_cap per ticker

**CompanyNote** — free-text investment notes per company with timestamps

**TradingUpdateAnalysis** — extracted guidance from trading updates with direction flags (ahead/inline/behind/unclear)

### LineType Enum (as stored VARCHAR strings — no migration needed for new values)

```
revenue, ebitda, operating_profit, adjusted_operating_profit,
finance_income, profit_before_tax, net_profit,
eps_reported, eps_adjusted, eps_adjusted_diluted,
net_debt, operating_cash_flow, capex, free_cash_flow,
dps, shares_outstanding, capital_employed, interest_expense, nav_per_share
```

### Net Debt Convention
Positive value = net debt (company owes money). Negative value = net cash. This matches standard analyst convention.

---

## Data Ingestion Design

### PDF Ingestion (primary workflow)

1. User uploads PDF, selects company (linked ticker/name dropdowns), and selects report date (UK date picker DD/MM/YYYY) and document type.
2. pdfplumber detects tables; junk filter removes tables with <2 cols, <2 rows, <15% numeric content, or avg label >80 chars. Reduces 81 detected tables to ~8–10 useful ones for a typical annual report.
3. User is shown a checklist of remaining tables. Each row has a 👁 preview button that shows a rendered version of the table alongside the original PDF page image.
4. User selects tables to analyse. On "Analyse Selected", each table goes through:
   - PyMuPDF renders the PDF page as a PNG image
   - Claude vision (`propose_mapping_from_image`) extracts structured data from the image: period columns, row labels, mapped line types, and numeric values
   - The AI returns `extracted_rows` (label, line_type, values) plus `row_mappings` for downstream compatibility
5. The review UI shows the AI-proposed mapping. User can adjust mappings, confirm, and the data is saved to FinancialLineItem records.
6. After saving, the confirmed rows are displayed inline below the Confirm button.

**Why vision over pdfplumber for extraction:** pdfplumber loses row labels and column headers when tables have merged cells or complex structure. Claude vision on the rendered page image preserves all context including surrounding labels, footnotes, and formatting that indicates whether numbers are in £m, £000, or pence.

### HTML Ingestion

URL paste → BeautifulSoup extraction → same review/mapping workflow.

### Restatements

Both original and restated figures stored with `version` and `is_canonical` flags. User selects which version is canonical for display.

### Reusable Mappings

TableMapping model stores per-company extraction configs. When the same company's next report is ingested, the previous mapping is surfaced as a starting point.

---

## Metrics Calculated

### Single-Period (per financial year/interim)

| Metric | Formula |
|---|---|
| EBITDA Margin | EBITDA / Revenue |
| Operating Margin | Operating Profit / Revenue |
| Net Margin | Net Profit / Revenue |
| ROCE | Operating Profit / Capital Employed |
| Interest Cover | Operating Profit / Interest Expense |
| Net Debt / EBITDA | Net Debt / EBITDA |
| Cash Conversion | Operating Cash Flow / Operating Profit |
| Dividend Cover | EPS Reported / DPS |
| P/E Reported | Share Price / EPS Reported |
| P/E Adjusted | Share Price / EPS Adjusted |
| EV/EBITDA | (Market Cap + Net Debt) / EBITDA |
| FCF Yield | FCF / Market Cap |
| Dividend Yield | DPS / Share Price |
| NAV Premium/Discount | (Price − NAV) / NAV (investment trusts only) |

### Cross-Period (requires multiple years)

| Metric | Notes |
|---|---|
| Revenue YoY Growth % | Period vs prior period |
| EPS Growth % (Reported and Adjusted) | Period vs prior period |
| EBITDA Growth % | Period vs prior period |
| Op Profit Growth % | Period vs prior period |
| Incremental ROCE | ΔOperating Profit / ΔCapital Employed (prior period) |
| Revenue CAGR 3yr / 5yr | Requires 4 / 6 periods of data |
| EPS CAGR 3yr / 5yr | Requires 4 / 6 periods of data |
| PEG Ratio | P/E Reported / (Forecast EPS Growth × 100) |

FCF is derived as Operating Cash Flow − |Capex| if not directly stored.

---

## Company Universe & Data Sources

### is_listed Flag
Companies that delist are marked `is_listed=False` but never deleted. They remain visible in Portfolio and Watchlist so historical analysis is preserved, but can be filtered out of the screener. The optional "mark others as delisted" toggle on bulk import handles this.

### Data Sources

| Source | Coverage | Method |
|---|---|---|
| Wikipedia (FTSE 100) | ~100 Main Market large-caps | `requests` with User-Agent header → `pd.read_html` |
| Wikipedia (FTSE 250) | ~250 Main Market mid-caps | Same |
| LSE Playwright scraper | AIM All-Share (~700 stocks), FTSE All-Share (~600 stocks) | Playwright headless Chromium, URL pagination `?page=N` |
| CSV/Excel upload | Any (user-supplied LSE instruments file) | pandas read, column auto-detection |
| yfinance search | Individual companies by name or ticker | Filters to `.L` suffix (UK listed) |
| Manual entry | Any company | Ticker + name form |

### LSE Playwright Scraper Design
The LSE constituent pages (e.g. `https://www.londonstockexchange.com/indices/ftse-aim-all-share/constituents/table`) are JS-rendered SPAs. Pagination is URL-based: append `?page=N` and increment until no rows are returned or the table selector times out. **Do not attempt button clicking** — the Next button is not reliably clickable in headless mode. Progress is reported per page via `st.empty()` status widget.

Column mapping from LSE CSV format: `Tidm` → ticker (append `.L`), `Instrument name` → name, `ICB Sector` → sector, `Market` → market, `Supersector` → sub_sector.

---

## UI Design Decisions

### Company Selection
Two linked selectboxes (ticker A-Z, name A-Z) that update each other on change. Implemented via `on_change` callbacks — **only works outside `st.form()`**. Inside forms, a single combined `"TICKER — Name"` selectbox is used instead. Both are in `pages/ui_helpers.py`.

### Date Input
`st.date_input(format="DD/MM/YYYY")` for UK date format throughout.

### Number Formatting
Values >1,000,000 shown as `Xm`, values >1,000 shown as `Xk`, percentages shown as `X.X%`.

---

## Agreed Planned Improvements (not yet implemented)

These were agreed during a Stockopedia-inspired design review session. Priority order to be confirmed.

### New LineTypes needed
- `book_value` — for ROE calculation
- `taxation_charge` — for effective tax rate

### New Metrics (calculator.py)
- **P/E Reported and Adjusted** — price (pence) ÷ EPS (pence). Already implemented; confirm units are consistent.
- **Price to FCF** — Market Cap ÷ FCF
- **ROE** — Net Profit ÷ Book Value
- **Effective Tax Rate %** — Taxation Charge ÷ Profit Before Tax
- **Dilution %** — (EPS Adjusted − EPS Adjusted Diluted) / EPS Adjusted. Shows how much outstanding share options dilute shareholders.
- **FCF per Share** — FCF ÷ Shares Outstanding (in pence)
- **DPS YoY Growth %** — inline growth row
- **DPS 5yr CAGR** — alongside revenue/EPS CAGRs

### Company Page Display Improvements (company.py)
- Inline YoY growth rate rows beneath absolute figure rows in the metrics table (e.g. Revenue row immediately followed by Revenue Growth % row)
- 5yr CAGR shown as a right-edge column on the metrics table
- Net debt sign convention label: positive shown as "Net Debt", negative shown as "Net Cash" with green colour

### Ingest Page Improvement
- Show confirmed/saved rows inline below the Confirm button so the user can verify extraction immediately without switching to the Company page.

---

## Trading Update Analysis

When a trading update is ingested:
1. AI reads the announcement text and extracts guidance statements per metric (revenue, EPS, etc.)
2. Each statement is classified: ahead / inline / behind / unclear (vs prior year trend)
3. An extrapolated value is calculated from the linear trend of historical data
4. Any significant discrepancy between stated guidance and extrapolated trend is flagged
5. Results shown on Company page → Trading Updates tab with colour-coded direction indicators

---

## Investment Trust Handling

Investment trusts get an additional metric: NAV per Share, and the NAV Premium/Discount % chart. The `company_type` field distinguishes trusts from equities and the chart is conditionally shown.

---

## Known Issues / Tech Notes

- **Streamlit module caching**: After adding new functions to an ingestion module, Streamlit may cache the old module version. Fix: restart the Streamlit server (`Ctrl+C` then `streamlit run app.py`).
- **SQLite enum as VARCHAR**: LineType enum values are stored as VARCHAR strings, not integers. New enum members can be added to the Python enum without any migration — SQLAlchemy will just store the new string value.
- **ALTER TABLE migrations**: New columns (e.g. `is_listed`) are added via `run_migrations()` in `db/database.py` using try/except (idempotent — safe to run on each startup).
- **pdfplumber duplicate column headers**: Some PDFs produce tables with duplicate empty column names. Fixed by `_safe_dataframe()` in `pages/ingest.py` which deduplicates before constructing a DataFrame.
- **Wikipedia 403**: `pd.read_html(url)` is blocked by Wikipedia. Fixed by fetching via `requests` with a `User-Agent` header, then passing the HTML string to `pd.read_html(io.StringIO(resp.text))`.
