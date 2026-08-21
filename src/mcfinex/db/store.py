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

from .dialect import for_dsn, split_statements

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Guarded so a typo in a caller cannot silently write to a column that does not
# exist, and so scraped keys can never widen the statement.
COMPANY_COLUMNS = (
    "name", "isin", "company_id", "sector", "broad_industry", "industry", "face_value",
    "market_cap", "current_price", "book_value", "stock_pe", "industry_pe",
    "dividend_yield", "roce", "roe", "outstanding_shares", "consolidated",
    "scan_for_results", "last_updated", "last_updated_quarter", "latest_period",
    "price_date",
)


class _Connection:
    """A connection that speaks whichever dialect it was opened for.

    Statements are written once in SQLite style and translated on the way out,
    so no call site has to know which database it is talking to.
    """

    def __init__(self, raw, dialect):
        self._raw = raw
        self.dialect = dialect
        self._transaction = None

    def execute(self, sql: str, params: Sequence[Any] = ()):
        return self._raw.execute(self.dialect.statement(sql), tuple(params))

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]):
        payload = [tuple(r) for r in rows]
        if not payload:
            # psycopg raises on an empty sequence where sqlite3 shrugs.
            return _Empty()
        cursor = self._raw.cursor()
        cursor.executemany(self.dialect.statement(sql), payload)
        return cursor

    def commit(self) -> None:
        self._raw.commit()

    def close(self) -> None:
        self._raw.close()

    def __enter__(self):
        # `with connection:` means different things in the two drivers. sqlite3
        # commits and leaves the connection open; psycopg closes it outright,
        # so the first transaction block would take the connection with it.
        # psycopg's transaction() is the equivalent scope.
        if self.dialect.is_postgres:
            self._transaction = self._raw.transaction()
            self._transaction.__enter__()
        else:
            self._raw.__enter__()
        return self

    def __exit__(self, *exc):
        if self.dialect.is_postgres:
            transaction, self._transaction = self._transaction, None
            return transaction.__exit__(*exc)
        return self._raw.__exit__(*exc)

    @property
    def raw(self):
        return self._raw


class _Empty:
    """Stands in for a cursor when there was nothing to execute."""

    rowcount = 0

    def __iter__(self):
        return iter(())

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class Store:
    """A connection to the MCFinEx database, SQLite or Postgres."""

    def __init__(self, path: str | Path):
        dsn = str(path)
        self.dialect = for_dsn(dsn)
        if self.dialect.is_postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:  # pragma: no cover - depends on install
                raise RuntimeError(
                    "This looks like a Postgres connection string, but the driver "
                    "is not installed. Run: pip install '.[postgres]'"
                ) from exc

            self.path = None
            raw = psycopg.connect(dsn, row_factory=dict_row, connect_timeout=20)
        else:
            self.path = Path(path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            raw = sqlite3.connect(self.path)
            raw.row_factory = sqlite3.Row
            raw.execute("PRAGMA foreign_keys = ON")
            raw.execute("PRAGMA journal_mode = WAL")
        self.conn = _Connection(raw, self.dialect)

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def create_schema(self) -> None:
        ddl = self.dialect.schema(SCHEMA_PATH.read_text())
        for statement in split_statements(ddl):
            self.conn.execute(statement)
        self._add_missing_columns()
        self.conn.commit()

    def _add_missing_columns(self) -> None:
        """Bring an existing database up to the current schema.

        ``CREATE TABLE IF NOT EXISTS`` leaves an older database untouched, so
        columns added later have to be applied separately rather than forcing a
        re-scrape of everything.
        """
        existing = self._company_columns()
        for column, ddl in (("price_date", "TEXT"), ("company_id", "INTEGER")):
            if column not in existing:
                self.conn.execute(f"ALTER TABLE companies ADD COLUMN {column} {ddl}")

    def _company_columns(self) -> set[str]:
        """Column names already on `companies`, however the database reports them."""
        if self.dialect.is_postgres:
            rows = self.conn.execute(
                "SELECT column_name AS name FROM information_schema.columns "
                "WHERE table_name = ?", ("companies",)
            )
        else:
            rows = self.conn.execute("PRAGMA table_info(companies)")
        return {r["name"] for r in rows}

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

    def upsert_companies(self, rows: Iterable[tuple[str, Mapping[str, Any]]],
                         columns: Sequence[str]) -> int:
        """Insert or update many companies in one statement.

        Seeding the universe one company at a time is 2,529 round-trips plus
        2,529 commits. Unnoticeable against a local file; roughly seventeen
        minutes from a CI runner to a database on another continent.
        """
        unknown = set(columns) - set(COMPANY_COLUMNS)
        if unknown:
            raise ValueError(f"unknown company column(s): {sorted(unknown)}")

        payload = [
            (ticker, *(_scalar(values.get(c)) for c in columns))
            for ticker, values in rows
        ]
        if not payload:
            return 0
        placeholders = ", ".join("?" for _ in range(len(columns) + 1))
        assignments = ", ".join(f"{c} = excluded.{c}" for c in columns)
        with self.conn:
            self.conn.executemany(
                f"INSERT INTO companies (ticker, {', '.join(columns)}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT(ticker) DO UPDATE SET {assignments}",
                payload,
            )
        return len(payload)

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

    def replace_schedule(self, ticker: str, rows: Iterable[tuple[str, str, str, float | None]]) -> int:
        """Replace a company's schedule detail, leaving its statements alone.

        Schedules are fetched separately from the page scrape, so they are
        replaced independently. A full re-scrape still clears them, because
        `replace_financials` wipes every row for the ticker -- cash from a
        previous quarter must not sit beside fresh statements.
        """
        payload = [(ticker, p, s, l, _scalar(v)) for p, s, l, v in rows]
        with self.conn:
            self.conn.execute(
                "DELETE FROM financials WHERE ticker = ? AND statement = ?",
                (ticker, "schedule"),
            )
            self.conn.executemany(
                "INSERT INTO financials (ticker, period, statement, label, value) "
                "VALUES (?, ?, ?, ?, ?)",
                payload,
            )
        return len(payload)

    def has_schedule(self, ticker: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM financials WHERE ticker = ? AND statement = 'schedule' LIMIT 1",
            (ticker,),
        ).fetchone()
        return row is not None

    def replace_valuations_bulk(self, models: Sequence[str],
                                rows: Iterable[tuple[str, str, str, Any]]) -> int:
        """Replace whole valuation models across every company at once.

        The per-company version costs fourteen statements each; over a network
        that is 35,616 round-trips for the universe, and a blip halfway leaves
        the database part-revalued.
        """
        stamp = date.today().isoformat()
        payload = [(t, m, f, _scalar(v), stamp) for t, m, f, v in rows
                   if not isinstance(v, (list, tuple))]
        placeholders = ", ".join("?" for _ in models)
        with self.conn:
            self.conn.execute(
                f"DELETE FROM valuations WHERE model IN ({placeholders})", tuple(models)
            )
            self.conn.executemany(
                "INSERT INTO valuations (ticker, model, field, value, computed_at) "
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
                "SELECT ticker FROM companies WHERE isin LIKE ? ORDER BY ticker",
                ("INF%",),
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

    def company(self, ticker: str):
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

    def all_companies(self) -> dict[str, Any]:
        """Every scraped company in one query, keyed by ticker."""
        return {
            r["ticker"]: r for r in self.conn.execute(
                "SELECT * FROM companies WHERE last_updated IS NOT NULL ORDER BY ticker"
            )
        }

    def all_series(self, wanted: Mapping[str, Sequence[str]],
                   ) -> dict[str, dict[tuple[str, str], list[float]]]:
        """Every requested line item for every company, newest first.

        One query instead of one per company per label. A full screen used to
        issue 43,398 queries; in-process against SQLite that is fine, but over a
        network it is eleven minutes of round-trips.
        """
        clauses, params = [], []
        for statement, labels in wanted.items():
            placeholders = ", ".join("?" for _ in labels)
            clauses.append(f"(statement = ? AND label IN ({placeholders}))")
            params.extend([statement, *labels])

        sql = (
            "SELECT ticker, statement, label, value FROM financials "
            f"WHERE value IS NOT NULL AND ({' OR '.join(clauses)}) "
            "ORDER BY ticker, statement, label, period DESC"
        )
        out: dict[str, dict[tuple[str, str], list[float]]] = {}
        for row in self.conn.execute(sql, params):
            key = (row["statement"], row["label"])
            out.setdefault(row["ticker"], {}).setdefault(key, []).append(row["value"])
        return out

    def all_valuations(self) -> dict[str, dict[str, dict[str, float | None]]]:
        """Every valuation field for every company, in one query."""
        out: dict[str, dict[str, dict[str, float | None]]] = {}
        for row in self.conn.execute("SELECT ticker, model, field, value FROM valuations"):
            out.setdefault(row["ticker"], {}).setdefault(row["model"], {})[row["field"]] = row["value"]
        return out

    def sector_pe_rows(self) -> list[tuple[str | None, str | None, float]]:
        """Industry, sector and P/E for every scraped, profitable company."""
        return [
            (r["industry"], r["sector"], r["stock_pe"]) for r in self.conn.execute(
                "SELECT industry, sector, stock_pe FROM companies "
                "WHERE stock_pe IS NOT NULL AND stock_pe > 0 AND last_updated IS NOT NULL"
            )
        ]

    def quarterly_history(self, ticker: str, label: str) -> list[tuple[str, float]]:
        """One quarterly line item oldest first, for trend analysis."""
        return [
            (r["period"], r["value"]) for r in self.conn.execute(
                "SELECT period, value FROM financials WHERE ticker = ? "
                "AND statement = 'quarters' AND label = ? AND value IS NOT NULL "
                "ORDER BY period",
                (ticker, label),
            )
        ]

    def schedule_latest(self, ticker: str) -> dict[str, float]:
        """Newest value for each schedule line item, keyed lower-case."""
        rows = self.conn.execute(
            "SELECT label, value FROM financials f WHERE ticker = ? AND statement = 'schedule' "
            "AND period = (SELECT MAX(period) FROM financials WHERE ticker = f.ticker "
            "AND statement = f.statement AND label = f.label)",
            (ticker,),
        )
        return {r["label"].strip().casefold(): r["value"] for r in rows if r["value"] is not None}

    def revision(self) -> str:
        """A token that changes whenever the data does.

        A local file has a modification time; a hosted database has not, so the
        cache needs something from the rows themselves. Cheap enough to run on
        every page load, and it changes after a scrape, a price refresh or an
        enrichment.
        """
        row = self.conn.execute(
            "SELECT MAX(last_updated) AS scraped, MAX(price_date) AS priced, "
            "COUNT(*) AS companies FROM companies"
        ).fetchone()
        valued = self.conn.execute(
            "SELECT MAX(computed_at) AS computed, COUNT(*) AS n FROM valuations"
        ).fetchone()
        return "|".join(str(x) for x in (
            row["scraped"], row["priced"], row["companies"],
            valued["computed"], valued["n"],
        ))

    def data_freshness(self) -> tuple[str | None, str | None]:
        """Newest price date and scrape date across the universe."""
        row = self.conn.execute(
            "SELECT MAX(price_date) AS priced, MAX(last_updated) AS scraped FROM companies"
        ).fetchone()
        return (row["priced"], row["scraped"]) if row else (None, None)

    def valuation_rows(self, ticker: str) -> list[tuple[str, str, float | None]]:
        """Every stored valuation field for one company."""
        return [
            (r["model"], r["field"], r["value"]) for r in self.conn.execute(
                "SELECT model, field, value FROM valuations WHERE ticker = ? "
                "ORDER BY model, field",
                (ticker,),
            )
        ]

    def compact(self) -> None:
        """Reclaim space after a bulk delete.

        Postgres cannot VACUUM inside a transaction, so it needs autocommit.
        """
        if self.dialect.is_postgres:
            raw = self.conn.raw
            previous = raw.autocommit
            raw.autocommit = True
            try:
                raw.execute("VACUUM")
            finally:
                raw.autocommit = previous
        else:
            self.conn.execute("VACUUM")

    def scraped_tickers(self) -> list[str]:
        """Companies that have actually been scraped, not just seeded from NSE."""
        return [
            r["ticker"]
            for r in self.conn.execute(
                "SELECT ticker FROM companies WHERE last_updated IS NOT NULL ORDER BY ticker"
            )
        ]

    def export_rows(self) -> Iterator[Any]:
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
