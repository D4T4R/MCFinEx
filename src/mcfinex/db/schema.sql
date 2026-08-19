-- SQLite schema for MCFinEx.
--
-- The Java build used five wide H2 tables with numbered columns (Q_EPS_1..5,
-- ENTERPRISE_VALUE1..5), which capped history at five periods and needed a
-- schema change to store anything new. This keeps one narrow table of facts
-- instead, so a company can carry as many periods as screener publishes.

CREATE TABLE IF NOT EXISTS companies (
    ticker                TEXT PRIMARY KEY,
    name                  TEXT,
    isin                  TEXT,
    -- Screener's internal id, which is the BSE scrip code. Needed by the
    -- schedules API that supplies cash and the current asset/liability split.
    company_id            INTEGER,
    sector                TEXT,
    broad_industry        TEXT,
    industry              TEXT,
    face_value            REAL,
    market_cap            REAL,   -- rupees crore
    current_price         REAL,
    book_value            REAL,
    stock_pe              REAL,
    industry_pe           REAL,
    dividend_yield        REAL,
    roce                  REAL,
    roe                   REAL,
    outstanding_shares    REAL,   -- crore
    -- Trading day the price came from. Screener rounds its displayed price to
    -- the nearest rupee, so `mcfinex prices` overwrites it with the exact NSE
    -- close and records which session that was.
    price_date            TEXT,
    consolidated          INTEGER NOT NULL DEFAULT 0,
    scan_for_results      TEXT    NOT NULL DEFAULT 'Y',
    last_updated          TEXT,   -- ISO date of the last successful scrape
    last_updated_quarter  TEXT,   -- fiscal quarter label, e.g. "2026-1"
    latest_period         TEXT    -- end date of the newest reported quarter
);

-- One row per (company, period, statement, line item). `statement` mirrors the
-- screener section: quarters, profit-loss, balance-sheet, cash-flow, ratios,
-- shareholding.
CREATE TABLE IF NOT EXISTS financials (
    ticker     TEXT NOT NULL,
    period     TEXT NOT NULL,
    statement  TEXT NOT NULL,
    label      TEXT NOT NULL,
    value      REAL,
    PRIMARY KEY (ticker, period, statement, label),
    FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_financials_lookup
    ON financials (ticker, statement, label, period);

-- Computed valuation outputs, one row per field so adding a model needs no
-- migration. `model` is ev_ebitda, eps_yearly or eps_quarterly.
CREATE TABLE IF NOT EXISTS valuations (
    ticker       TEXT NOT NULL,
    model        TEXT NOT NULL,
    field        TEXT NOT NULL,
    value        REAL,
    computed_at  TEXT NOT NULL,
    PRIMARY KEY (ticker, model, field),
    FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE
);

-- Companies whose results are due this quarter, seeded by the NSE loader or by
-- hand; replaces STOCKS_RESULTS_FOR_CURRENT_QUARTER.
CREATE TABLE IF NOT EXISTS result_calendar (
    ticker            TEXT NOT NULL,
    result_date       TEXT NOT NULL,
    financial_quarter TEXT NOT NULL,
    PRIMARY KEY (ticker, result_date, financial_quarter)
);
