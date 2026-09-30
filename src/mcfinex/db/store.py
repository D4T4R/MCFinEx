"""SQLite persistence.

Every write goes through a parameterised statement. The Java original built
SQL by concatenating scraped strings into ``MERGE INTO ... VALUES ('...')``,
so any company name containing an apostrophe broke the query outright -- and
anything else in the page text went straight into the database as SQL.
"""

from __future__ import annotations

import json
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
            # Never return psycopg's value. A Transaction may return True to
            # suppress the exception it rolled back on, which would let a failed
            # write look like a successful one: the caller carries on and logs
            # success while the database still holds the previous data.
            transaction.__exit__(*exc)
            return False
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
            # autocommit is not a relaxation here, it is what makes the
            # transaction blocks below work at all.
            #
            # Without it psycopg opens an implicit transaction on the first
            # statement -- including a plain SELECT -- and leaves it open. A
            # later `with self.conn:` then sees transaction_status != IDLE and
            # downgrades itself to a SAVEPOINT, whose exit emits RELEASE and no
            # COMMIT. close() then rolls the lot back, silently.
            #
            # That is what left the hosted valuations stale: `mcfinex prices`
            # committed the prices (nothing had read yet, so that block was a
            # real transaction), then revalue_all read three tables, and the
            # 78k-row write that followed became a savepoint and was discarded
            # on close while the CLI logged success.
            #
            # With autocommit on, reads leave the connection IDLE, so every
            # `with self.conn:` is an outer transaction that really commits.
            raw = psycopg.connect(dsn, row_factory=dict_row, connect_timeout=20,
                                  autocommit=True)
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

    def unscraped_tickers(self, limit: int | None = None) -> list[str]:
        """Companies seeded from a bhavcopy but never scraped, ticker order.

        A seeded row carries a symbol, an ISIN and a price and nothing else. It
        is not screened -- screening needs financials -- so it is invisible in
        the app until this backlog drains. New listings arrive here.

        One query rather than `needs_refresh` per company: `scrape --all` walks
        every stored ticker asking individually, which against a hosted database
        is some 2,600 round trips before a single page is fetched.
        """
        sql = ("SELECT ticker FROM companies WHERE last_updated IS NULL "
               "ORDER BY ticker")
        params: list[Any] = []
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [r["ticker"] for r in self.conn.execute(sql, params)]

    def unenriched_tickers(self, limit: int | None = None) -> list[str]:
        """Scraped companies with no schedule detail yet, oldest scrape first.

        One query rather than `has_schedule` per company: the caller is picking
        a batch out of ~2,500, and asking individually would be 2,500 round
        trips to a hosted database before a single page is fetched.

        Ordered by ticker so the backlog drains predictably across runs.
        Companies only seeded from the bhavcopy are excluded: there is nothing
        to enrich until they have been scraped.

        A company screener publishes no schedules for never gains rows, so it is
        re-offered on every run. That is deliberate -- schedules do appear later,
        and a company is not permanently barren -- but it means the tail of the
        backlog is a fixed retry cost of four requests each. `cmd_enrich` counts
        those separately so the size of that tail is visible.
        """
        sql = (
            "SELECT c.ticker FROM companies c "
            "WHERE c.last_updated IS NOT NULL AND NOT EXISTS ("
            "  SELECT 1 FROM financials f "
            "  WHERE f.ticker = c.ticker AND f.statement = 'schedule') "
            "ORDER BY c.ticker"
        )
        params: list[Any] = []
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [r["ticker"] for r in self.conn.execute(sql, params)]

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

        # Read it back. A silently rolled-back transaction leaves the previous
        # valuations in place, which is indistinguishable from success at the
        # call site and leaves the site serving figures computed against an old
        # price -- exactly the failure this method exists to prevent.
        stored = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM valuations WHERE model IN ({placeholders}) "
            "AND computed_at = ?",
            (*models, stamp),
        ).fetchone()["n"]
        if stored != len(payload):
            raise RuntimeError(
                f"valuation write did not persist: expected {len(payload):,} rows "
                f"stamped {stamp}, found {stored:,}"
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

    #: Everything keyed by ticker that a rename has to carry with it. Adding a
    #: ticker-keyed table without adding it here leaves its rows behind, pointing
    #: at a symbol that no longer exists -- which for `financials` would mean
    #: quietly losing a company's entire history.
    TICKER_TABLES = ("financials", "valuations", "result_calendar", "alert_state")

    def has_history(self, ticker: str) -> bool:
        """Whether anything has actually been scraped for this ticker."""
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM financials WHERE ticker = ?", (ticker,)
        ).fetchone()
        return bool(row and row["n"])

    def rename_ticker(self, old: str, new: str) -> None:
        """Move a company and all its history onto a new symbol.

        The exchange renames companies, and the seeder keys on ticker, so
        without this a rename silently forks the company: a fresh empty row
        under the new symbol, and the old one keeping every quarter of history
        while dropping out of the price feed. The orphan then goes on being
        screened forever on whatever price it held the day the rename landed.

        Any row already sitting under ``new`` is discarded first -- that is the
        empty row the seeder just made. :meth:`has_history` is how a caller
        checks this is safe; doing it here as well would hide the ambiguous case
        rather than refuse it.

        The order is forced by the foreign keys, which cascade on delete but not
        on update. Writing the new parent first means the children always have
        one to point at; deleting the old parent last removes nothing, because
        by then nothing references it.
        """
        if old == new:
            return
        columns = ", ".join(COMPANY_COLUMNS)
        with self.conn:
            for table in self.TICKER_TABLES:
                self.conn.execute(f"DELETE FROM {table} WHERE ticker = ?", (new,))
            self.conn.execute("DELETE FROM companies WHERE ticker = ?", (new,))
            self.conn.execute(
                f"INSERT INTO companies (ticker, {columns}) "
                f"SELECT ?, {columns} FROM companies WHERE ticker = ?",
                (new, old),
            )
            for table in self.TICKER_TABLES:
                self.conn.execute(
                    f"UPDATE {table} SET ticker = ? WHERE ticker = ?", (new, old))
            self.conn.execute("DELETE FROM companies WHERE ticker = ?", (old,))

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

    def tickers_by_isin(self) -> dict[str, str]:
        """ISIN to stored ticker, for matching a second exchange to a company.

        The ISIN is the only identifier NSE and BSE share -- symbols differ
        between them -- so it is what a BSE row has to be matched on.

        **Ambiguous ISINs are omitted.** ``isin`` carries no unique constraint,
        and when a company is renamed the seeder creates a second row under the
        new symbol, leaving one security under two tickers: HEG and HEGAM both
        hold INE545A01024. Picking one of those would be a guess, and the wrong
        guess writes today's price onto the row the exchange has stopped feeding,
        where it looks perfectly current. Ambiguity is not identity, so these are
        simply not matchable; :meth:`duplicate_isins` reports them instead.
        """
        return {isin: t[0] for isin, t in self.isin_groups().items() if len(t) == 1}

    def duplicate_isins(self) -> dict[str, list[str]]:
        """ISINs held by more than one company row.

        Almost always a rename: the seeder keys on ticker, so a company that
        changes symbol gains a second row and the old one stops being priced
        while still being screened on its last known price.
        """
        return {isin: t for isin, t in self.isin_groups().items() if len(t) > 1}

    def isin_groups(self) -> dict[str, list[str]]:
        """Every stored ISIN and the ticker or tickers holding it.

        The single source for both of the above, so "unambiguous" and
        "duplicated" cannot come to disagree about the same row.
        """
        out: dict[str, list[str]] = {}
        for row in self.conn.execute(
            "SELECT isin, ticker FROM companies "
            "WHERE isin IS NOT NULL AND isin <> '' ORDER BY isin, ticker"
        ):
            out.setdefault(row["isin"], []).append(row["ticker"])
        return out

    def known_isins(self) -> set[str]:
        """Every ISIN already tracked, scraped or merely seeded.

        Includes the unscraped, because the question this answers is whether
        seeding a company again would duplicate one -- and a row seeded last
        night but not yet scraped is still a row.
        """
        return {
            r["isin"] for r in self.conn.execute(
                "SELECT isin FROM companies WHERE isin IS NOT NULL AND isin <> ''"
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

    def all_quarterly_history(
        self, labels: Sequence[str],
    ) -> dict[str, dict[str, list[tuple[str, float]]]]:
        """:meth:`quarterly_history` for every company at once, oldest first.

        Publishing needs trends for the whole shortlist, and asking per company
        per label is the N+1 that :meth:`all_series` exists to avoid -- roughly
        five thousand round-trips, which is minutes against a hosted database
        and nothing at all against a local file. Periods are kept, so this
        cannot simply reuse ``all_series``, which returns values only.
        """
        placeholders = ", ".join("?" for _ in labels)
        out: dict[str, dict[str, list[tuple[str, float]]]] = {}
        if not labels:
            return out
        rows = self.conn.execute(
            "SELECT ticker, label, period, value FROM financials "
            f"WHERE statement = 'quarters' AND label IN ({placeholders}) "
            "AND value IS NOT NULL ORDER BY ticker, label, period",
            tuple(labels),
        )
        for row in rows:
            out.setdefault(row["ticker"], {}).setdefault(row["label"], []).append(
                (row["period"], row["value"])
            )
        return out

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

    # ---------------------------------------------------------- alert state

    def alert_state(self) -> dict[str, dict[str, Any]]:
        """What every company looked like when alerts were last delivered.

        Returned as plain dicts rather than :class:`mcfinex.alerts.Snapshot`, so
        persistence does not depend on the rule engine and the rule engine stays
        importable without a database.
        """
        out: dict[str, dict[str, Any]] = {}
        for row in self.conn.execute(
            "SELECT ticker, tier, actionable, upside_pct, verdicts FROM alert_state"
        ):
            try:
                verdicts = json.loads(row["verdicts"] or "{}")
            except json.JSONDecodeError:
                # A row we cannot read is a row whose history is gone. Treating
                # it as absent means the company is seen as new and its alerts
                # do not fire, which is the quiet failure; the loud one would be
                # crashing the nightly job over one malformed column.
                verdicts = {}
            out[row["ticker"]] = {
                "tier": row["tier"],
                "actionable": bool(row["actionable"]),
                "upside_pct": row["upside_pct"],
                "verdicts": verdicts if isinstance(verdicts, dict) else {},
            }
        return out

    def save_alert_state(self, states: Mapping[str, Mapping[str, Any]],
                         when: date | str | None = None) -> int:
        """Record what was just delivered, so the next run compares against it.

        Called only after a successful send. Advancing this on a failed delivery
        is how an alert is lost for good: the transition is consumed, and the
        next run sees no change to report.
        """
        stamp = _scalar(when or date.today())
        payload = [
            (ticker, s.get("tier"), int(bool(s.get("actionable"))),
             s.get("upside_pct"), json.dumps(s.get("verdicts") or {},
                                             separators=(",", ":")), stamp)
            for ticker, s in states.items()
        ]
        if not payload:
            return 0
        with self.conn:
            self.conn.executemany(
                "INSERT INTO alert_state "
                "(ticker, tier, actionable, upside_pct, verdicts, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(ticker) DO UPDATE SET tier = excluded.tier, "
                "actionable = excluded.actionable, upside_pct = excluded.upside_pct, "
                "verdicts = excluded.verdicts, updated_at = excluded.updated_at",
                payload,
            )
        return len(payload)


def _scalar(value: Any) -> Any:
    """Coerce a Python value into something sqlite3 will bind."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, date):
        return value.isoformat()
    return value
