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
    -- The company's own description of its business, verbatim from the page
    -- the scrape already downloads. Stored raw: tidying it is presentation and
    -- will change, and re-scraping the universe to pick up a better regex
    -- would be an absurd way to ship a wording fix.
    about                 TEXT,
    -- Products or services read out of `about` by a model, as JSON:
    -- {"source_sha": "...", "tags": [...]}. The digest is of the description
    -- they were read from, so a re-scrape that rewrites it makes them
    -- detectably stale rather than quietly wrong. An empty list is an answer
    -- (the description names no product), not a gap to retry.
    tags                  TEXT,
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
    -- Which board the company trades on: Main or SME. Stored rather than
    -- re-derived, because the series code it comes from is only in the
    -- bhavcopy, and the screen is published long after that file is read.
    board                 TEXT,
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

-- What each company looked like the last time alerts were delivered. An alert
-- fires on a transition, so without this every run resends the same names until
-- the reader mutes them.
--
-- No foreign key to companies: this is the record of what was already said, and
-- deleting a company should not silently make its alerts eligible to fire again
-- if it is ever re-added.
--
-- `verdicts` is a JSON object rather than a column per signal. The set of
-- signals is not fixed, and a column each would need a migration every time one
-- is added -- to store a value nothing queries on.
CREATE TABLE IF NOT EXISTS alert_state (
    ticker      TEXT PRIMARY KEY,
    tier        TEXT,
    actionable  INTEGER NOT NULL DEFAULT 0,
    upside_pct  REAL,
    verdicts    TEXT NOT NULL DEFAULT '{}',
    updated_at  TEXT NOT NULL
);

-- Who was on the shortlist at the end of each publish, so a reader can be told
-- what changed rather than only what is true now.
--
-- Kept apart from `alert_state`, though both record what the screen said last
-- time. That one advances only when every push was delivered, because
-- advancing it early consumes a transition and the alert is lost rather than
-- delayed. Nothing that merely wants to show a count may be able to cause
-- that, so the two are separate rows written by different commands.
--
-- Keyed on the date rather than the timestamp: publishing twice in one day
-- should correct that day's record, not append a second one that makes the
-- next comparison read "nothing changed".
--
-- `tickers` is a JSON array rather than a row per company. The only question
-- asked of it is which names entered and which left -- a set difference, done
-- in Python -- and a row each would be some 1,800 a night to answer it.
CREATE TABLE IF NOT EXISTS screen_snapshot (
    taken_on  TEXT PRIMARY KEY,
    taken_at  TEXT NOT NULL,
    tickers   TEXT NOT NULL DEFAULT '[]',
    universe  INTEGER NOT NULL DEFAULT 0
);
