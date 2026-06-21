# Investment Analyser — Implementation Plan

## Context
A personal investment tool for a private investor focused on long-term UK equities and investment trusts (~30 stocks currently owned). The tool needs to ingest financial reports (PDF and HTML), extract structured financial data with AI assistance, calculate investment metrics, and present historical trends in tabular and graphical form. A secondary goal is screening the full UK stock universe to discover new investments. The core value is understanding business fundamentals over time, not short-term price movements.

---

## Tech Stack
- **Python + Streamlit** — UI framework
- **SQLite via SQLAlchemy** — local storage, single file, zero config
- **pdfplumber / camelot-py** — PDF table extraction
- **requests + BeautifulSoup** — HTML parsing from Investegate, LSE RNS, company IR pages
- **yfinance** — historical share prices (`.L` suffix for UK stocks)
- **Plotly** — interactive charts
- **Claude API** — AI table mapping, trading update analysis, guidance extraction
- **python-dotenv** — `ANTHROPIC_API_KEY` and other config in `.env`

---

## Data Model

### `companies`
`ticker (PK), name, market, sector, sub_sector, isin, company_type (equity | investment_trust), blacklisted, in_watchlist, in_portfolio, created_at`

### `documents`
`id, ticker (FK), document_type (annual | interim | trading_update | nav_announcement), source_url, file_path, period_end_date, uploaded_at, processed_at`

### `financial_periods`
`id, ticker (FK), period_end_date, period_type (annual | interim), is_audited, source_document_id (FK)`

### `financial_line_items`
`id, period_id (FK), line_type (enum — see metrics), value, is_adjusted (bool), adjustment_description, version (int, for restatements), is_canonical (bool — user selects authoritative version)`

Enum line types: `revenue, ebitda, operating_profit, net_profit, eps_reported, eps_adjusted, net_debt, operating_cash_flow, capex, free_cash_flow, dps, shares_outstanding, capital_employed, interest_expense, nav_per_share`

### `adjustments`
`id, period_id (FK), line_type, adjustment_name, adjustment_value, is_accepted (bool — user toggles per adjustment)`

### `table_mappings`
`id, ticker, table_name, document_type, mapping_config (JSON — column/row mappings to line_type enum), last_used_at`
Reusable per company. Treated as tentative — flagged for re-review if structure changes.

### `extracted_tables`
`id, document_id (FK), table_index, raw_data (JSON), mapping_id (FK nullable), review_status (pending | approved | rejected)`

### `forecast_entries`
`id, ticker (FK), metric_type, value, period_end_date, entered_at, source_notes`
Timestamped so consensus drift is visible over time. Used for PEG ratio.

### `company_notes`
`id, ticker (FK), note_text, created_at`
Timestamped decision/thesis log for watchlist and portfolio companies.

### `share_prices`
`id, ticker (FK), price_date, close, market_cap`
Pulled via yfinance on demand.

### `trading_update_analysis`
`id, document_id (FK), metric_type, guidance_direction (ahead | inline | behind | unclear), guidance_text (extracted quote), extrapolated_value, flagged_discrepancy (bool), created_at`

---

## Calculated Metrics
All computed on-the-fly from stored line items + share prices. Never stored except `trading_update_analysis`.

| Metric | Formula |
|--------|---------|
| Revenue growth % | (Rev[t] − Rev[t-1]) / Rev[t-1] |
| EBITDA margin | EBITDA / Revenue |
| Operating margin | Op Profit / Revenue |
| EPS growth (reported & adjusted) | YoY % |
| 3yr / 5yr CAGR | Compound growth on key lines |
| P/E (reported & adjusted) | Price / EPS |
| EV/EBITDA | (Market cap + Net debt) / EBITDA |
| Net debt / EBITDA | Net debt / EBITDA |
| FCF yield | FCF / Market cap |
| Dividend yield | DPS / Price |
| Dividend cover | EPS / DPS |
| ROCE | Op Profit / Capital Employed |
| Incremental ROCE | ΔOp Profit[t,t-1] / ΔCapital Employed[t-1,t-2] |
| Interest cover | Op Profit / Interest |
| Cash conversion | Operating CF / Op Profit |
| PEG | P/E / Forecast EPS growth (from `forecast_entries`) |
| NAV premium/discount | (Price − NAV per share) / NAV per share (investment trusts only) |

---

## Document Ingestion Pipeline

### PDF path
1. User selects ticker, `period_end_date`, `document_type`, uploads file
2. `pdfplumber` (primary) / `camelot-py` (fallback for complex tables) extract all tables
3. For each table: check `table_mappings` for existing company mapping
   - If found → apply tentatively, mark for review
   - If not found → send table to Claude API for identification and mapping proposal
4. **Review UI**: side-by-side display — raw extracted table (left) vs. mapped output with line_type labels (right)
5. User confirms/edits → persisted to `financial_line_items`
6. Mapping saved/updated in `table_mappings` for future reuse

### HTML path
1. User pastes URL (Investegate, LSE RNS, or company IR page)
2. `requests` + `BeautifulSoup` fetch and parse; extract tables and narrative text blocks
3. Same table identification → mapping → review pipeline as PDF
4. Narrative text stored on `documents.raw_text` for trading update processing

### Trading update analysis
1. Claude API receives narrative text → extracts forward-looking statements as structured JSON: `{metric, direction, quote, implied_value}`
2. System calculates simple linear extrapolation from last 3 periods for each metric
3. Comparison: flag each metric as ahead / inline / behind / unclear vs. extrapolation
4. Results persisted to `trading_update_analysis`, displayed on Company page

### Restatement handling
- Both original and restated figures stored with `version` field
- System detects when a new document's "prior period" figures differ from stored `is_canonical=true` values
- User prompted to review discrepancy; selects canonical version
- All metrics calculated using canonical version only

---

## UI Pages

### 1. Universe
- Import LSE instruments CSV (ticker, name, sector, sub-sector, ISIN, market)
- Supplement with yfinance: market cap, basic profitability flag, cash generation flag
- Filterable table: sector, market cap range, profitable (Y/N), cash generative (Y/N)
- Per-row blacklist toggle; blacklisted rows hidden by default (toggle to show)

### 2. Watchlist
- Table of watchlist companies with sector, key metrics summary
- Add company (by ticker), remove company
- Timestamped notes log per company

### 3. Portfolio
- Same structure as Watchlist but for owned companies
- Separate add/remove from Watchlist

### 4. Company (detail page)
- Select company by ticker
- **Metrics table**: all periods as columns, all line items and calculated metrics as rows; click any row → line chart rendered below
- **Standard charts** (Plotly, interactive):
  - Revenue, EBITDA, Operating Profit (bar, annual + interim overlay)
  - EPS reported vs. adjusted over time
  - Net debt / EBITDA over time
  - ROCE and Incremental ROCE over time
  - Operating CF, Capex, FCF over time
  - Cash conversion % over time
  - DPS + Dividend cover over time
  - Share price with document event markers
- **Trading update panel**: latest analysis with ahead/inline/behind flags per metric
- **Forecast entries**: log of entered EPS forecasts with timestamps
- **Notes log**: timestamped narrative entries

### 5. Ingest
- Tab 1: Upload PDF → select ticker, date, type → run extraction → review tables
- Tab 2: Paste URL → fetch → run extraction → review tables
- Review UI: raw table | mapped output side-by-side, confirm or edit before persisting

---

## Build Order

Build a **vertical slice on a single company** before expanding to the full portfolio. This validates the hardest parts (PDF parsing, AI extraction, metric calculation) with real data before scale.

1. **Foundation** — project structure, SQLAlchemy models, DB migration, `.env` config, Streamlit shell with page routing
2. **Ingestion** — PDF + HTML parsing pipeline, Claude API table extraction, side-by-side review UI, mapping persistence
3. **Company page** — financial history table with click-to-chart, all standard charts, metric calculations
4. **Trading updates** — AI guidance extraction, linear extrapolation, structured comparison display
5. **Watchlist / Portfolio** — add/remove, timestamped notes log
6. **Universe screener** — LSE instruments import, yfinance enrichment, filtering, blacklisting

---

## Project Structure
```
InvestmentAnalyser/
├── app.py                  # Streamlit entry point, page routing
├── .env                    # ANTHROPIC_API_KEY, etc.
├── requirements.txt
├── db/
│   ├── models.py           # SQLAlchemy ORM models
│   └── database.py         # Engine, session factory, migrations
├── ingestion/
│   ├── pdf_parser.py       # pdfplumber/camelot extraction
│   ├── html_parser.py      # requests/BS4 extraction
│   ├── ai_extractor.py     # Claude API calls for mapping + trading update analysis
│   └── mapping_store.py    # Load/save table_mappings
├── metrics/
│   └── calculator.py       # All metric calculations from ORM objects
├── pages/
│   ├── universe.py
│   ├── watchlist.py
│   ├── portfolio.py
│   ├── company.py
│   └── ingest.py
└── data/
    └── investments.db      # SQLite database file
```

---

## Verification
1. `pip install -r requirements.txt && streamlit run app.py` — app loads with navigation
2. Upload a real annual report PDF for one owned company → tables extracted → review UI shows side-by-side → data persists to DB
3. Open Company page → metrics table populates → clicking a row renders a chart
4. Paste an Investegate trading update URL → guidance extracted → ahead/inline/behind flags shown
5. Import LSE instruments CSV → Universe page shows filterable company list
