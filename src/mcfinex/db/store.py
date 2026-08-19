"""SQLite persistence.

Every write goes through a parameterised statement. The Java original built
SQL by concatenating scraped strings into ``MERGE INTO ... VALUES ('...')``,
so any company name containing an apostrophe broke the query outright -- and
anything else in the page text went straight into the database as SQL.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Guarded so a typo in a caller cannot silently write to a column that does not
# exist, and so scraped keys can never widen the statement.
COMPANY_COLUMNS = (
    "name", "isin", "sector", "broad_industry", "industry", "face_value",
    "market_cap", "current_price", "book_value", "stock_pe", "industry_pe",
    "dividend_yield", "roce", "roe", "outstanding_shares", "consolidated",
    "scan_for_results", "last_updated", "last_updated_quarter", "latest_period",
    "price_date",
)


class Store:
    """A connection to the MCFinEx SQLite database."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def create_schema(self) -> None:
        self.conn.executescript(SCHEMA_PATH.read_text())
        self._add_missing_columns()
        self.conn.commit()

    def _add_missing_columns(self) -> None:
        """Bring an existing database up to the current schema.

        ``CREATE TABLE IF NOT EXISTS`` leaves an older database untouched, so
        columns added later have to be applied separately rather than forcing a
        re-scrape of everything.
        """
        existing = {r["name"] for r in self.conn.execute("PRAGMA table_info(companies)")}
        for column, ddl in (("price_date", "TEXT"),):
            if column not in existing:
                self.conn.execute(f"ALTER TABLE companies ADD COLUMN {column} {ddl}")

    # ---------------------------------------------------------------- writes

    def upsert_company(self, ticker: str, values: Mapping[str, Any]) -> None:
        """Insert or update a company row, touching only the keys supplied."""
        cols = [c for c in COMPANY_COLUMNS if c in values]
        unknown = set(values) - set(COMPANY_COLUMNS)
        if unknown:
            raise ValueError(f"unknown company column(s): {sorted(unknown)}")

        placeholders = ", ".join("?" for _ in cols)
        assignments = ", ".join(f"{c} = excluded.{c}" for c in cols)
        sql = (
            f"INSERT INTO companies (ticker{''.join(', ' + c for c in cols)}) "
            f"VALUES (?{', ' + placeholders if cols else ''}) "
            f"ON CONFLICT(ticker) DO UPDATE SET {assignments}"
            if cols
            else "INSERT INTO companies (ticker) VALUES (?) ON CONFLICT(ticker) DO NOTHING"
        )
        self.conn.execute(sql, [ticker, *(_scalar(values[c]) for c in cols)])
        self.conn.commit()

    def update_prices(self, prices: Mapping[str, float | None], as_of: date | str) -> int:
        """Set the closing price for companies already known, ignoring the rest.

        Only updates existing rows: the bhavcopy lists every instrument on the
        exchange, and a price alone is not a reason to start tracking one.
        """
        stamp = as_of.isoformat() if isinstance(as_of, date) else as_of
        payload = [
            (price, stamp, ticker)
            for ticker, price in prices.items()
            if price is not None
        ]
        with self.conn:
            cursor = self.conn.executemany(
                "UPDATE companies SET current_price = ?, price_date = ? WHERE ticker = ?",
                payload,
            )
        return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0

    def replace_financials(self, ticker: str, rows: Iterable[tuple[str, str, str, float | None]]) -> int:
        """Replace this company's financial facts. Rows are (period, statement, label, value)."""
        payload = [(ticker, p, s, l, _scalar(v)) for p, s, l, v in rows]
        with self.conn:
            self.conn.execute("DELETE FROM financials WHERE ticker = ?", (ticker,))
            self.conn.executemany(
                "INSERT INTO financials (ticker, period, statement, label, value) "
                "VALUES (?, ?, ?, ?, ?)",
                payload,
            )
        return len(payload)

    def replace_valuations(self, ticker: str, model: str, fields: Mapping[str, Any]) -> int:
        """Replace one valuation model's output for a company."""
        stamp = date.today().isoformat()
        payload = [
            (ticker, model, field, _scalar(value), stamp)
            for field, value in fields.items()
            if not isinstance(value, (list, tuple))  # series live in `financials`
        ]
        with self.conn:
            self.conn.execute(
                "DELETE FROM valuations WHERE ticker = ? AND model = ?", (ticker, model)
            )
            self.conn.executemany(
                "INSERT INTO valuations (ticker, model, field, value, computed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                payload,
            )
        return len(payload)

    # ---------------------------------------------------------------- reads

    def fund_unit_tickers(self) -> list[str]:
        """Stored rows that are ETFs or mutual fund units, not companies.

        Identified by the ISIN prefix: INE is an equity share, INF a fund unit.
        These trade in the EQ series so they arrive through the bhavcopy, but
        they have no financial statements and cannot be screened.
        """
        return [
            r["ticker"] for r in self.conn.execute(
                "SELECT ticker FROM companies WHERE isin LIKE 'INF%' ORDER BY ticker"
            )
        ]

    def remove(self, tickers: Sequence[str]) -> int:
        """Delete companies and everything hanging off them."""
        if not tickers:
            return 0
        rows = [(t,) for t in tickers]
        with self.conn:
            self.conn.executemany("DELETE FROM financials WHERE ticker = ?", rows)
            self.conn.executemany("DELETE FROM valuations WHERE ticker = ?", rows)
            self.conn.executemany("DELETE FROM companies WHERE ticker = ?", rows)
        return len(tickers)

    def tickers(self, *, only_scannable: bool = True) -> list[str]:
        sql = "SELECT ticker FROM companies"
        if only_scannable:
            sql += " WHERE scan_for_results = 'Y'"
        sql += " ORDER BY ticker"
        return [r["ticker"] for r in self.conn.execute(sql)]

    def company(self, ticker: str) -> sqlite3.Row | None:
        cur = self.conn.execute("SELECT * FROM companies WHERE ticker = ?", (ticker,))
        return cur.fetchone()

    def needs_refresh(self, ticker: str, quarter: str, today: date | None = None) -> bool:
        """Whether a company still needs scraping for ``quarter``.

        Skips anything already checked today or already carrying this quarter's
        results, which is what keeps a re-run from re-fetching the whole list.
        """
        row = self.company(ticker)
        if row is None:
            return True
        stamp = (today or date.today()).isoformat()
        if row["last_updated"] == stamp:
            return False
        return row["last_updated_quarter"] != quarter

    def series(self, ticker: str, statement: str, *labels: str, limit: int | None = None) -> list[float]:
        """A line item's history, newest first -- the order the workbook expects.

        Accepts several spellings and returns the first that has data, because
        screener labels the same line differently for banks and NBFCs. See
        :mod:`mcfinex.labels`.
        """
        for label in labels:
            sql = (
                "SELECT value FROM financials "
                "WHERE ticker = ? AND statement = ? AND label = ? AND value IS NOT NULL "
                "ORDER BY period DESC"
            )
            params: list[Any] = [ticker, statement, label]
            if limit:
                sql += " LIMIT ?"
                params.append(limit)
            values = [r["value"] for r in self.conn.execute(sql, params)]
            if values:
                return values
        return []

    def valuation_fields(self, ticker: str, model: str) -> dict[str, float | None]:
        return {
            r["field"]: r["value"]
            for r in self.conn.execute(
                "SELECT field, value FROM valuations WHERE ticker = ? AND model = ?",
                (ticker, model),
            )
        }

    def scraped_tickers(self) -> list[str]:
        """Companies that have actually been scraped, not just seeded from NSE."""
        return [
            r["ticker"]
            for r in self.conn.execute(
                "SELECT ticker FROM companies WHERE last_updated IS NOT NULL ORDER BY ticker"
            )
        ]

    def export_rows(self) -> Iterator[sqlite3.Row]:
        """Every company joined to its valuation fields, for the Excel export."""
        return self.conn.execute(
            """
            SELECT c.*, v.model, v.field, v.value AS valuation_value
            FROM companies c
            LEFT JOIN valuations v ON v.ticker = c.ticker
            ORDER BY c.ticker, v.model, v.field
            """
        )

    def seed_result_calendar(self, rows: Sequence[tuple[str, str, str]]) -> int:
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO result_calendar (ticker, result_date, financial_quarter) "
                "VALUES (?, ?, ?)",
                rows,
            )
        return len(rows)


def _scalar(value: Any) -> Any:
    """Coerce a Python value into something sqlite3 will bind."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, date):
        return value.isoformat()
    return value
