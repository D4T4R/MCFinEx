from datetime import date, datetime, timezone

import pytest

from mcfinex.db.store import Store


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "test.db") as s:
        s.create_schema()
        yield s


class TestCompanies:
    def test_upsert_then_read_back(self, store):
        store.upsert_company("ACME", {"name": "Acme Ltd", "current_price": 10.5})
        assert store.company("ACME")["name"] == "Acme Ltd"

    def test_upsert_updates_only_supplied_columns(self, store):
        store.upsert_company("ACME", {"name": "Acme Ltd", "current_price": 10.5})
        store.upsert_company("ACME", {"current_price": 12.0})
        row = store.company("ACME")
        assert row["current_price"] == 12.0
        assert row["name"] == "Acme Ltd"

    def test_quotes_in_a_company_name_are_safe(self, store):
        # The Java built SQL by concatenation, so this broke the statement
        # outright -- and anything else on the page became executable SQL.
        nasty = "O'Reilly's; DROP TABLE companies;--"
        store.upsert_company("ORLY", {"name": nasty})
        assert store.company("ORLY")["name"] == nasty
        assert store.company("ORLY") is not None

    def test_unknown_column_is_rejected(self, store):
        with pytest.raises(ValueError, match="unknown company column"):
            store.upsert_company("ACME", {"not_a_column": 1})

    def test_missing_company_is_none(self, store):
        assert store.company("NOPE") is None


class TestPrices:
    def test_updates_price_and_records_the_session(self, store):
        store.upsert_company("ACME", {"current_price": 206.0})
        assert store.update_prices({"ACME": 205.58}, date(2026, 8, 18)) == 1
        row = store.company("ACME")
        # Screener rounds to the rupee; the bhavcopy close is exact.
        assert row["current_price"] == 205.58
        assert row["price_date"] == "2026-08-18"

    def test_ignores_tickers_that_are_not_tracked(self, store):
        store.upsert_company("ACME", {})
        store.update_prices({"ACME": 10.0, "NOTTRACKED": 99.0}, date(2026, 8, 18))
        assert store.company("NOTTRACKED") is None

    def test_skips_listings_with_no_close(self, store):
        store.upsert_company("ACME", {"current_price": 50.0})
        store.update_prices({"ACME": None}, date(2026, 8, 18))
        assert store.company("ACME")["current_price"] == 50.0

    def test_accepts_an_iso_string_date(self, store):
        store.upsert_company("ACME", {})
        store.update_prices({"ACME": 1.5}, "2026-08-18")
        assert store.company("ACME")["price_date"] == "2026-08-18"


class TestSchemaMigration:
    def test_price_date_is_added_to_an_older_database(self, tmp_path):
        path = tmp_path / "old.db"
        with Store(path) as s:
            s.create_schema()
            s.conn.execute("ALTER TABLE companies DROP COLUMN price_date")
            s.conn.commit()
            assert "price_date" not in {
                r["name"] for r in s.conn.execute("PRAGMA table_info(companies)")
            }
        with Store(path) as s:
            s.create_schema()  # must repair rather than require a rebuild
            assert "price_date" in {
                r["name"] for r in s.conn.execute("PRAGMA table_info(companies)")
            }


    def test_about_is_added_to_an_older_database(self, tmp_path):
        # The column arrived after the hosted database did, and nothing runs
        # `init` against it -- the nightly seeds, prices and publishes into a
        # database that already exists.
        path = tmp_path / "old.db"
        with Store(path) as s:
            s.create_schema()
            s.conn.execute("ALTER TABLE companies DROP COLUMN about")
            s.conn.commit()
        with Store(path) as s:
            s.create_schema()
            assert "about" in {
                r["name"] for r in s.conn.execute("PRAGMA table_info(companies)")
            }

    def test_a_description_round_trips(self, tmp_path):
        with Store(tmp_path / "a.db") as s:
            s.create_schema()
            s.upsert_company("ACME", {"about": "Makes widgets for export"})
            assert s.company("ACME")["about"] == "Makes widgets for export"


class TestFinancials:
    def test_series_is_newest_first(self, store):
        store.upsert_company("ACME", {})
        store.replace_financials("ACME", [
            ("2023-03-31", "profit-loss", "EPS in Rs", 1.0),
            ("2024-03-31", "profit-loss", "EPS in Rs", 2.0),
            ("2025-03-31", "profit-loss", "EPS in Rs", 3.0),
        ])
        assert store.series("ACME", "profit-loss", "EPS in Rs") == [3.0, 2.0, 1.0]

    def test_series_respects_the_limit(self, store):
        store.upsert_company("ACME", {})
        store.replace_financials("ACME", [
            (f"202{i}-03-31", "profit-loss", "EPS in Rs", float(i)) for i in range(5)
        ])
        assert store.series("ACME", "profit-loss", "EPS in Rs", limit=2) == [4.0, 3.0]

    def test_falls_back_to_an_alias_label(self, store):
        # Banks are labelled "Borrowing"; everyone else "Borrowings".
        store.upsert_company("BANK", {})
        store.replace_financials("BANK", [
            ("2025-03-31", "balance-sheet", "Borrowing", 500.0),
        ])
        assert store.series("BANK", "balance-sheet", "Borrowings", "Borrowing") == [500.0]

    def test_prefers_the_first_label_that_has_data(self, store):
        store.upsert_company("ACME", {})
        store.replace_financials("ACME", [
            ("2025-03-31", "balance-sheet", "Borrowings", 100.0),
            ("2025-03-31", "balance-sheet", "Borrowing", 999.0),
        ])
        assert store.series("ACME", "balance-sheet", "Borrowings", "Borrowing") == [100.0]

    def test_no_alias_matches_returns_empty(self, store):
        store.upsert_company("ACME", {})
        assert store.series("ACME", "balance-sheet", "Nope", "AlsoNope") == []

    def test_replace_clears_the_previous_scrape(self, store):
        store.upsert_company("ACME", {})
        store.replace_financials("ACME", [("2024-03-31", "profit-loss", "EPS in Rs", 1.0)])
        store.replace_financials("ACME", [("2025-03-31", "profit-loss", "EPS in Rs", 9.0)])
        assert store.series("ACME", "profit-loss", "EPS in Rs") == [9.0]


class TestRefreshPolicy:
    def test_unknown_company_needs_a_scrape(self, store):
        assert store.needs_refresh("NEW", "2026-1")

    def test_already_checked_today_is_skipped(self, store):
        store.upsert_company("ACME", {"last_updated": date.today().isoformat()})
        assert not store.needs_refresh("ACME", "2026-1")

    def test_stale_quarter_needs_a_rescrape(self, store):
        store.upsert_company("ACME", {
            "last_updated": "2020-01-01", "last_updated_quarter": "2020-4",
        })
        assert store.needs_refresh("ACME", "2026-1")

    def test_current_quarter_is_left_alone(self, store):
        store.upsert_company("ACME", {
            "last_updated": "2020-01-01", "last_updated_quarter": "2026-1",
        })
        assert not store.needs_refresh("ACME", "2026-1")


class TestValuations:
    def test_round_trip(self, store):
        store.upsert_company("ACME", {})
        store.replace_valuations("ACME", "eps_yearly", {"target_price": 42.0})
        assert store.valuation_fields("ACME", "eps_yearly") == {"target_price": 42.0}

    def test_series_fields_are_not_stored_as_scalars(self, store):
        store.upsert_company("ACME", {})
        store.replace_valuations("ACME", "ev_ebitda", {
            "ebitda": [1.0, 2.0], "target_price": 5.0,
        })
        assert set(store.valuation_fields("ACME", "ev_ebitda")) == {"target_price"}

    def test_scraped_tickers_excludes_seeded_only(self, store):
        store.upsert_company("SEEDED", {"isin": "IN123"})
        store.upsert_company("DONE", {"last_updated": "2026-01-01"})
        assert store.scraped_tickers() == ["DONE"]


class TestRevision:
    """The cache key every Streamlit page load computes before rendering.

    It only has to do two things: change when the data changes, and be cheap.
    It had no tests, and the expensive half -- a COUNT(*) over 92,000 valuation
    rows, 958 ms against the hosted database -- was 99% of the page's latency
    spent confirming a row count had not moved.
    """

    def test_it_is_stable_when_nothing_changes(self, store):
        store.upsert_company("ACME", {"last_updated": "2026-01-01"})
        assert store.revision() == store.revision()

    def test_a_scrape_moves_it(self, store):
        store.upsert_company("ACME", {"last_updated": "2026-01-01"})
        before = store.revision()
        store.upsert_company("ACME", {"last_updated": "2026-01-02"})
        assert store.revision() != before

    def test_a_price_refresh_moves_it(self, store):
        store.upsert_company("ACME", {})
        before = store.revision()
        store.update_prices({"ACME": 10.0}, "2026-01-02")
        assert store.revision() != before

    def test_a_new_company_moves_it(self, store):
        before = store.revision()
        store.upsert_company("NEW", {"isin": "IN1"})
        assert store.revision() != before

    def test_a_same_day_revalue_moves_it(self, store):
        """What replaced the row count, and why the stamp became a timestamp.

        Rewriting a value in place changes no row count, so the old COUNT(*)
        never saw it. MAX(computed_at) only sees it because the stamp carries
        a time: at the day granularity it used to have, a second revalue on
        one day was indistinguishable from the first -- so an enrich run at
        noon could rewrite the universe and leave every reader on the cached
        screen until midnight.
        """
        store.upsert_company("ACME", {})
        store.replace_valuations("ACME", "ev_ebitda", {"target_price": 10.0})
        before = store.revision()
        store.replace_valuations("ACME", "ev_ebitda", {"target_price": 99.0})
        assert store.revision() != before

    def test_an_enrich_that_adds_a_field_moves_it(self, store):
        # The case that proved dropping COUNT(*) was not free: enrichment adds
        # current_assets to an existing model, same day, no company row touched.
        store.upsert_company("ACME", {})
        store.replace_valuations("ACME", "derived", {"ebit": 1.0})
        before = store.revision()
        store.replace_valuations("ACME", "derived", {"ebit": 1.0, "current_assets": 5.0})
        assert store.revision() != before

    def test_tagging_moves_it(self, store):
        """Otherwise `mcfinex tags` writes chips nobody can see.

        Tags are read by the cards but touch no date and no row count, so
        without counting them the page would keep serving the cached screen
        until something unrelated happened to change.
        """
        store.upsert_company("ACME", {"about": "makes valves"})
        before = store.revision()
        store.upsert_company("ACME", {"tags": '{"source_sha":"a","tags":["Valves"]}'})
        assert store.revision() != before

    def test_it_survives_a_database_without_the_newer_columns(self, tmp_path):
        """The bug this caught, before it shipped.

        `revision` runs in the Streamlit process, which never migrates. Naming
        `tags` unconditionally took every page down with UndefinedColumn
        against the hosted database -- which has no such column until a nightly
        happens to run create_schema -- and would have kept it down from the
        moment the code deployed.
        """
        path = tmp_path / "older.db"
        with Store(path) as s:
            s.create_schema()
            s.conn.execute("ALTER TABLE companies DROP COLUMN tags")
            s.conn.execute("ALTER TABLE companies DROP COLUMN about")
            s.conn.commit()
        with Store(path) as s:
            s.upsert_company("ACME", {"last_updated": "2026-01-01"})
            assert s.revision()            # does not raise
            before = s.revision()
            s.upsert_company("ACME", {"last_updated": "2026-01-02"})
            assert s.revision() != before  # and still tracks what it can see

    def test_a_description_arriving_moves_it(self, store):
        store.upsert_company("ACME", {})
        before = store.revision()
        store.upsert_company("ACME", {"about": "makes valves"})
        assert store.revision() != before


class TestScreenSnapshot:
    """The record of who was shortlisted, which is what "what changed" needs."""

    def at(self, store, day, tickers, universe=100):
        # Midday UTC is 17:30 IST, so the run falls on the same date in both
        # and these tests are about ordering rather than the timezone. The
        # boundary itself is pinned separately below.
        return store.save_screen_snapshot(
            tickers, universe, when=datetime(2026, 11, day, 12, 0, tzinfo=timezone.utc))

    def test_it_round_trips_the_shortlist(self, store):
        self.at(store, 10, ["ACME", "ZEE"])
        [snap] = store.recent_snapshots()
        assert snap["tickers"] == {"ACME", "ZEE"}
        assert snap["taken_on"] == "2026-11-10"
        assert snap["universe"] == 100

    def test_a_second_run_the_same_day_corrects_rather_than_appends(self, store):
        # Two snapshots on one day would make the next comparison diff a run
        # against itself and report "nothing changed".
        self.at(store, 10, ["ACME"])
        self.at(store, 10, ["ACME", "ZEE"])
        snaps = store.recent_snapshots()
        assert len(snaps) == 1
        assert snaps[0]["tickers"] == {"ACME", "ZEE"}

    def test_snapshots_come_back_newest_first(self, store):
        self.at(store, 10, ["OLD"])
        self.at(store, 12, ["NEW"])
        assert [s["taken_on"] for s in store.recent_snapshots()] == [
            "2026-11-12", "2026-11-10"]

    def test_history_is_capped(self, store, monkeypatch):
        monkeypatch.setattr(Store, "SNAPSHOT_HISTORY", 3)
        for day in range(1, 7):
            self.at(store, day, [f"T{day}"])
        kept = store.recent_snapshots(limit=50)
        assert len(kept) == 3
        # The newest are the ones worth keeping.
        assert [s["taken_on"] for s in kept] == [
            "2026-11-06", "2026-11-05", "2026-11-04"]

    def test_no_snapshots_is_an_empty_list_not_an_error(self, store):
        # The state every database is in until the first publish runs.
        assert store.recent_snapshots() == []

    def test_a_missing_table_reads_as_no_history_rather_than_crashing(self, store):
        """What the hosted database looks like between deploy and first publish.

        The Streamlit pages never migrate -- they are deployed publicly and
        read a database the nightly job owns -- so the table does not exist
        until a publish creates it. A raw SELECT took the whole Ideas page down
        with a stack trace, when the honest answer is the one the page already
        renders for a database with no runs recorded yet.
        """
        with store.conn:
            store.conn.execute("DROP TABLE screen_snapshot")
        assert store.recent_snapshots() == []

    def test_duplicates_collapse_so_the_count_is_of_companies(self, store):
        assert self.at(store, 10, ["ACME", "ACME", "ZEE"]) == 2

    def test_the_date_is_the_indian_one_not_the_host_s(self, store):
        """The nightly lands about 01:10 IST, which is still yesterday in UTC.

        Keyed on the UTC date, a run finishing just after midnight in Mumbai
        is filed under the previous day -- overwriting that day's record, and
        showing a reader a date one behind their own calendar.
        """
        # 19:40 UTC on the 18th is 01:10 IST on the 19th.
        store.save_screen_snapshot(
            ["ACME"], 1,
            when=datetime(2026, 11, 18, 19, 40, tzinfo=timezone.utc))
        [snap] = store.recent_snapshots()
        assert snap["taken_on"] == "2026-11-19"
        assert snap["taken_at"].startswith("2026-11-19T01:10")

    def test_two_runs_either_side_of_ist_midnight_are_separate_days(self, store):
        # 23:30 IST on the 18th, then 01:10 IST on the 19th. Under UTC keying
        # both land on the 18th and the second silently replaces the first.
        store.save_screen_snapshot(
            ["ACME"], 1, when=datetime(2026, 11, 18, 18, 0, tzinfo=timezone.utc))
        store.save_screen_snapshot(
            ["ACME", "ZEE"], 1, when=datetime(2026, 11, 18, 19, 40, tzinfo=timezone.utc))
        assert [s["taken_on"] for s in store.recent_snapshots()] == [
            "2026-11-19", "2026-11-18"]

    def test_a_naive_timestamp_is_read_as_utc(self, store):
        # Rather than as the host's local time, which would make the stored
        # date depend on where the job happened to run.
        store.save_screen_snapshot(["ACME"], 1,
                                   when=datetime(2026, 11, 18, 19, 40))
        assert store.recent_snapshots()[0]["taken_on"] == "2026-11-19"


class TestTickersNotScrapedSince:
    """Resuming a forced re-scrape that ran out of time.

    The cutoff is what tells a finished company from an unfinished one, because
    --force ignores needs_refresh and --limit always takes the same leading N.
    """

    def test_it_takes_only_what_predates_the_cutoff(self, store):
        store.upsert_company("OLD", {"last_updated": "2026-10-30"})
        store.upsert_company("DONE", {"last_updated": "2026-10-31"})
        assert store.tickers_not_scraped_since("2026-10-31") == ["OLD"]

    def test_a_company_never_scraped_is_included(self, store):
        # `NULL < '2026-10-31'` is NULL, not true, so without an explicit IS
        # NULL the seeded companies silently drop out -- exactly the ones most
        # in need of scraping.
        store.upsert_company("SEEDED", {"isin": "IN123"})
        assert store.tickers_not_scraped_since("2026-10-31") == ["SEEDED"]

    def test_the_cutoff_is_exclusive_so_a_run_does_not_redo_its_own_work(self, store):
        # Pass the date the interrupted run started: everything it stamped that
        # day is excluded, so re-dispatching cannot repeat it.
        store.upsert_company("SAME_DAY", {"last_updated": "2026-10-31"})
        assert store.tickers_not_scraped_since("2026-10-31") == []

    def test_unscannable_companies_stay_out(self, store):
        # The SME board is priced but deliberately not screened; a resume must
        # not be the thing that drags those into a scrape.
        store.upsert_company("SME", {"last_updated": "2026-01-01",
                                     "scan_for_results": "N"})
        store.upsert_company("MAIN", {"last_updated": "2026-01-01"})
        assert store.tickers_not_scraped_since("2026-10-31") == ["MAIN"]
        assert "SME" in store.tickers_not_scraped_since(
            "2026-10-31", only_scannable=False)

    def test_it_is_ordered_so_a_run_is_reproducible(self, store):
        for t in ("ZEE", "ACME", "MID"):
            store.upsert_company(t, {"last_updated": "2026-01-01"})
        assert store.tickers_not_scraped_since("2026-10-31") == ["ACME", "MID", "ZEE"]


class TestPostgresConnection:
    """The connection must be autocommit, and it is not a style choice.

    psycopg opens an implicit transaction on the first statement, a plain SELECT
    included, and leaves it open. A later `with connection:` then finds
    transaction_status != IDLE, downgrades itself to a SAVEPOINT, and exits with
    RELEASE and no COMMIT -- so close() discards the write. That is what left the
    hosted valuations stale while `mcfinex prices` logged success: the prices
    committed because nothing had read yet, and the revalue that followed three
    SELECTs did not.
    """

    def connect_kwargs(self, monkeypatch):
        import sys
        import types

        seen = {}

        def connect(dsn, **kwargs):
            seen.update(kwargs)
            return types.SimpleNamespace(
                execute=lambda *a, **k: None, close=lambda: None,
            )

        fake = types.ModuleType("psycopg")
        fake.connect = connect
        rows = types.ModuleType("psycopg.rows")
        rows.dict_row = object()
        monkeypatch.setitem(sys.modules, "psycopg", fake)
        monkeypatch.setitem(sys.modules, "psycopg.rows", rows)
        Store("postgresql://u:p@h/db")
        return seen

    def test_autocommit_is_on(self, monkeypatch):
        assert self.connect_kwargs(monkeypatch)["autocommit"] is True

    def test_sqlite_is_untouched(self, store):
        # The fix must not change local behaviour, which was always correct.
        assert not store.dialect.is_postgres
        store.upsert_company("AAA", {})
        store.replace_valuations_bulk(["ev_ebitda"],
                                      [("AAA", "ev_ebitda", "target_price", 1.0)])
        assert store.valuation_fields("AAA", "ev_ebitda") == {"target_price": 1.0}


class FakePostgres:
    """Enough of psycopg's transaction protocol to show why autocommit matters.

    Two rules, both taken from psycopg 3.3 rather than invented:

    * a statement on an IDLE connection that is not in autocommit implicitly
      begins a transaction (``BEGIN``), leaving it INTRANS;
    * ``transaction()`` records ``_outer_transaction = status == IDLE``, and on
      exit emits ``COMMIT`` only when that is true -- otherwise it opened a
      savepoint and emits ``RELEASE``.

    Closing discards anything not committed, as a real connection does.
    """

    def __init__(self, autocommit=False):
        self.autocommit = autocommit
        self.idle = True
        self.log: list[str] = []
        self.written: list[str] = []
        self._committed: list[str] = []

    def _statement(self):
        if self.idle and not self.autocommit:
            self.log.append("BEGIN")
            self.idle = False

    def execute(self, sql, params=()):
        self._statement()
        if not sql.strip().upper().startswith("SELECT"):
            self.written.append(sql)
        return self

    def cursor(self):
        return self

    def executemany(self, sql, rows):
        self._statement()
        self.written.append(sql)
        return self

    def transaction(self):
        outer = self.idle
        fake = self

        class Block:
            def __enter__(self):
                fake._statement()
                return self

            def __exit__(self, *exc):
                if outer:
                    fake.log.append("COMMIT")
                    fake._committed.extend(fake.written)
                    fake.written.clear()
                    fake.idle = True
                else:
                    fake.log.append("RELEASE")
                return False

        return Block()

    def commit(self):
        if self.idle:
            return                      # psycopg returns early when IDLE
        self.log.append("COMMIT")
        self._committed.extend(self.written)
        self.written.clear()
        self.idle = True

    def close(self):
        self.log.append("ROLLBACK(close)")
        self.written.clear()

    @property
    def durable(self):
        return self._committed


class TestReadThenWriteCommits:
    """A write that follows a read must still reach disk.

    This is the shape of `mcfinex prices`: update prices, then revalue, which
    reads three tables before writing. Without autocommit the write became a
    savepoint and was discarded on close, silently.
    """

    def connection(self, autocommit):
        from mcfinex.db.dialect import for_dsn
        from mcfinex.db.store import _Connection

        raw = FakePostgres(autocommit=autocommit)
        return raw, _Connection(raw, for_dsn("postgresql://u:p@h/db"))

    def revalue_shaped(self, conn):
        conn.execute("SELECT * FROM companies")        # revalue_all reads first
        conn.execute("SELECT * FROM financials")
        with conn:
            conn.executemany("INSERT INTO valuations ...", [(1,), (2,)])

    def test_without_autocommit_the_write_is_lost(self):
        raw, conn = self.connection(autocommit=False)
        self.revalue_shaped(conn)
        raw.close()
        assert "COMMIT" not in raw.log and "RELEASE" in raw.log
        assert raw.durable == []

    def test_with_autocommit_the_write_commits(self):
        raw, conn = self.connection(autocommit=True)
        self.revalue_shaped(conn)
        raw.close()
        assert "COMMIT" in raw.log
        assert [q.split(" SET")[0] for q in raw.durable] == ["INSERT INTO valuations ..."]

    def test_a_write_with_no_preceding_read_always_committed(self):
        # Why prices persisted while valuations did not: nothing had read yet,
        # so that block was an outer transaction even without autocommit.
        raw, conn = self.connection(autocommit=False)
        with conn:
            conn.executemany("UPDATE companies SET current_price = ?", [(1,)])
        raw.close()
        assert [q.split(" SET")[0] for q in raw.durable] == ["UPDATE companies"]


class TestPostgresTransactionScope:
    """`with connection:` must not be able to hide a failure.

    psycopg's Transaction returns True from __exit__ when it has rolled back and
    wants the exception suppressed. Returning that verbatim made a failed write
    indistinguishable from a successful one at the call site.
    """

    def connection(self, transaction):
        from mcfinex.db.dialect import for_dsn
        from mcfinex.db.store import _Connection

        class Raw:
            def transaction(self):
                return transaction

        return _Connection(Raw(), for_dsn("postgresql://u:p@h/db"))

    def test_a_suppressing_transaction_does_not_swallow_the_error(self):
        class Suppressing:
            def __enter__(self): return self
            def __exit__(self, *exc): return True      # "I handled it"

        conn = self.connection(Suppressing())
        with pytest.raises(ValueError):
            with conn:
                raise ValueError("write failed")

    def test_the_transaction_is_still_closed(self):
        closed = []

        class Recording:
            def __enter__(self): return self
            def __exit__(self, *exc):
                closed.append(exc[0])
                return False

        conn = self.connection(Recording())
        with conn:
            pass
        assert closed == [None]

    def test_a_clean_block_is_unaffected(self):
        class Plain:
            def __enter__(self): return self
            def __exit__(self, *exc): return False

        conn = self.connection(Plain())
        with conn:
            pass


class TestBulkValuations:
    """Replacing whole models at once, and proving the replacement landed.

    This is the path that revalues the universe nightly. It reported success
    against the hosted database while the stored figures did not move, so the
    write is read back rather than trusted.
    """

    def rows(self, *tickers, target=100.0):
        return [(t, "ev_ebitda", "target_price", target) for t in tickers]

    def test_round_trip(self, store):
        for t in ("AAA", "BBB"):
            store.upsert_company(t, {})
        assert store.replace_valuations_bulk(["ev_ebitda"], self.rows("AAA", "BBB")) == 2
        assert store.valuation_fields("AAA", "ev_ebitda") == {"target_price": 100.0}

    def test_it_replaces_rather_than_accumulates(self, store):
        store.upsert_company("AAA", {})
        store.replace_valuations_bulk(["ev_ebitda"], self.rows("AAA", target=100.0))
        store.replace_valuations_bulk(["ev_ebitda"], self.rows("AAA", target=250.0))
        assert store.valuation_fields("AAA", "ev_ebitda") == {"target_price": 250.0}

    def test_untouched_models_survive(self, store):
        store.upsert_company("AAA", {})
        store.replace_valuations("AAA", "eps_yearly", {"target_price": 7.0})
        store.replace_valuations_bulk(["ev_ebitda"], self.rows("AAA"))
        assert store.valuation_fields("AAA", "eps_yearly") == {"target_price": 7.0}

    def test_series_values_are_dropped_not_stored(self, store):
        store.upsert_company("AAA", {})
        rows = [("AAA", "ev_ebitda", "ebitda", [1.0, 2.0]),
                ("AAA", "ev_ebitda", "target_price", 100.0)]
        # The count must describe what was written, or the readback compares
        # against a number the database was never asked to store.
        assert store.replace_valuations_bulk(["ev_ebitda"], rows) == 1

    def test_nothing_to_write_is_not_an_error(self, store):
        assert store.replace_valuations_bulk(["ev_ebitda"], []) == 0

    def test_a_write_that_does_not_persist_raises(self, store, monkeypatch):
        """The failure that prompted the guard: a silent rollback.

        Without this the caller logs "recomputed valuations for 2,544
        companies" and the site keeps serving the previous prices.
        """
        store.upsert_company("AAA", {})
        real = store.conn.executemany
        monkeypatch.setattr(store.conn, "executemany",
                            lambda sql, rows: real(sql, []))
        with pytest.raises(RuntimeError, match="did not persist"):
            store.replace_valuations_bulk(["ev_ebitda"], self.rows("AAA"))


class TestBulkUpsert:
    """Seeding the universe must not cost one round trip per company."""

    def test_inserts_many(self, store):
        rows = [(f"T{i}", {"isin": f"IN{i}", "current_price": float(i)}) for i in range(50)]
        assert store.upsert_companies(rows, ("isin", "current_price")) == 50
        assert store.company("T7")["isin"] == "IN7"

    def test_updates_existing_without_duplicating(self, store):
        store.upsert_companies([("ACME", {"current_price": 1.0})], ("current_price",))
        store.upsert_companies([("ACME", {"current_price": 2.0})], ("current_price",))
        assert store.company("ACME")["current_price"] == 2.0
        assert len(store.all_companies()) == 0  # never scraped, so not "all companies"

    def test_untouched_columns_survive(self, store):
        store.upsert_company("ACME", {"name": "Acme Ltd", "last_updated": "2026-01-01"})
        store.upsert_companies([("ACME", {"current_price": 5.0})], ("current_price",))
        assert store.company("ACME")["name"] == "Acme Ltd"

    def test_unknown_column_is_rejected(self, store):
        with pytest.raises(ValueError, match="unknown company column"):
            store.upsert_companies([("ACME", {"nope": 1})], ("nope",))

    def test_empty_input_is_a_no_op(self, store):
        assert store.upsert_companies([], ("current_price",)) == 0

    def test_a_missing_value_becomes_null(self, store):
        store.upsert_companies([("ACME", {})], ("current_price",))
        assert store.company("ACME")["current_price"] is None


class TestUnenrichedBacklog:
    """Picking the next batch to enrich, in one query rather than 2,500."""

    def scraped(self, store, *tickers):
        for t in tickers:
            store.upsert_company(t, {"last_updated": "2026-01-01"})

    def test_lists_scraped_companies_without_schedules(self, store):
        self.scraped(store, "AAA", "BBB")
        assert store.unenriched_tickers() == ["AAA", "BBB"]

    def test_enriched_companies_drop_out(self, store):
        self.scraped(store, "AAA", "BBB")
        store.replace_schedule("AAA", [("2026-03-31", "schedule", "Cash Equivalents", 5.0)])
        assert store.unenriched_tickers() == ["BBB"]

    def test_seeded_but_never_scraped_are_excluded(self, store):
        # A bhavcopy listing has no financials yet, so there is nothing to expand.
        store.upsert_company("SEEDED", {"isin": "IN123"})
        self.scraped(store, "DONE")
        assert store.unenriched_tickers() == ["DONE"]

    def test_other_statements_do_not_count_as_enriched(self, store):
        # Only 'schedule' rows mean enrichment; a normal scrape must not qualify.
        self.scraped(store, "AAA")
        store.replace_financials("AAA", [("2026-03-31", "balance-sheet", "Borrowings", 1.0)])
        assert store.unenriched_tickers() == ["AAA"]

    def test_limit_takes_a_batch(self, store):
        self.scraped(store, "AAA", "BBB", "CCC")
        assert store.unenriched_tickers(limit=2) == ["AAA", "BBB"]

    def test_the_backlog_drains_across_runs(self, store):
        self.scraped(store, "AAA", "BBB", "CCC")
        first = store.unenriched_tickers(limit=2)
        for t in first:
            store.replace_schedule(t, [("2026-03-31", "schedule", "Cash Equivalents", 1.0)])
        assert store.unenriched_tickers(limit=2) == ["CCC"]

    def test_nothing_left_is_empty_not_an_error(self, store):
        assert store.unenriched_tickers(limit=10) == []


class TestFreshnessCaption:
    """What the pages tell a reader about how current the data is.

    They used to print `mcfinex prices`, which only the owner can run and only
    against their own machine -- on the public site that is an instruction
    nobody reading it can follow.
    """

    def test_reports_both_dates_readably(self, store):
        from mcfinex.ui import freshness

        store.upsert_company("AAA", {"last_updated": "2026-08-21"})
        store.update_prices({"AAA": 10.0}, "2026-09-04")
        assert freshness(store) == (
            "Prices as of 4 Sep 2026 · fundamentals to 21 Aug 2026 · "
            "prices refresh nightly"
        )

    def test_an_empty_database_says_something_sane(self, store):
        from mcfinex.ui import freshness

        assert freshness(store) == "Screened from stored data."

    def test_a_price_with_no_scrape_still_reads(self, store):
        from mcfinex.ui import freshness

        store.upsert_company("AAA", {})
        store.update_prices({"AAA": 10.0}, "2026-09-04")
        assert freshness(store).startswith("Prices as of 4 Sep 2026")
        assert "fundamentals" not in freshness(store)

    def test_a_junk_date_is_not_rendered(self, store):
        # Never surface a raw stored value that is not a date.
        from mcfinex.ui import freshness

        store.upsert_company("AAA", {"last_updated": "not-a-date"})
        assert freshness(store) == "Screened from stored data."


class TestBusinessSummary:
    """Screener's preamble removed, the company's own words kept."""

    def test_it_drops_the_year_and_the_restated_name(self):
        from mcfinex.ui import business_summary

        assert business_summary(
            "Incorporated in 1999, Credo Brands Marketing Ltd is in the business "
            "of selling of fashion casual garments and accessories under the "
            "brand name MUFTI"
        ) == ("Selling of fashion casual garments and accessories under the "
              "brand name MUFTI")

    def test_it_handles_limited_spelled_out(self):
        from mcfinex.ui import business_summary

        assert business_summary(
            "Incorporated in 1994, Tata Technologies Limited is a global "
            "engineering services company offering Product Development."
        ) == "Global engineering services company offering Product Development."

    def test_text_with_no_preamble_is_left_alone(self):
        # The patterns are anchored, so a description that opens with substance
        # must survive untouched rather than lose its first clause.
        from mcfinex.ui import business_summary

        original = ("E2E Networks is an Nse-listed AI-focused hyper scale cloud "
                    "platform, offering advanced cloud GPUs.")
        assert business_summary(original) == original

    def test_nothing_to_show_is_none_not_an_empty_string(self):
        # So a caller can omit the block rather than render a blank line.
        from mcfinex.ui import business_summary

        assert business_summary(None) is None
        assert business_summary("") is None
        assert business_summary("   ") is None

    def test_a_description_that_is_only_preamble_survives(self):
        """Stripping must never leave nothing.

        Better to show the boilerplate than to show an empty panel where the
        company's own description should be.
        """
        from mcfinex.ui import business_summary

        assert business_summary("Incorporated in 1999,") == "Incorporated in 1999,"

    def test_stacked_preambles_are_all_removed(self):
        """"is engaged in the business of" is three preambles in a row.

        One pass leaves "The business of owning and operating...", which reads
        like a fragment. Found on Advani Hotels while checking the rendered
        page rather than the function.
        """
        from mcfinex.ui import business_summary

        assert business_summary(
            "Incorporated in 1987, Advani Hotels & Resorts (India) Ltd is "
            "engaged in the business of owning and operating a five-star "
            "deluxe beach resort in Goa"
        ) == "Owning and operating a five-star deluxe beach resort in Goa"

    def test_a_footnote_marker_is_removed_here_too(self):
        """The scraper strips it, and this does not rely on that.

        Rows written before the scraper learned to strip it still carry one,
        and a stray "[1]" on the page reads as a broken link.
        """
        from mcfinex.ui import business_summary

        assert business_summary("Makes widgets [1]") == "Makes widgets"

    def test_whitespace_is_collapsed(self):
        from mcfinex.ui import business_summary

        assert business_summary("Makes   widgets\n  and gadgets") == (
            "Makes widgets and gadgets")


class TestShortDate:
    """The compact form, for a date sitting in a row of counts."""

    def test_it_is_day_first(self):
        # How a date is written in India. The ISO string it comes from is the
        # only form that would be read month-first here, so the ambiguous
        # 11/20/26 ordering is the one thing this must not produce.
        from mcfinex.ui import _short

        assert _short("2026-11-20") == "20/11/26"

    def test_single_digits_are_padded_so_the_width_is_stable(self):
        # The point of the short form is a column that lines up.
        from mcfinex.ui import _short

        assert _short("2026-01-05") == "05/01/26"
        assert len(_short("2026-01-05")) == len(_short("2026-11-20"))

    def test_a_full_timestamp_is_accepted(self):
        # `taken_at` carries a time and an offset; only the date is wanted.
        from mcfinex.ui import _short

        assert _short("2026-11-20T01:10:00+05:30") == "20/11/26"

    def test_nothing_in_nothing_out(self):
        from mcfinex.ui import _short

        assert _short(None) is None
        assert _short("") is None
        assert _short("not-a-date") is None

    def test_no_cli_command_is_suggested_to_visitors(self, store):
        from mcfinex.ui import freshness

        store.upsert_company("AAA", {"last_updated": "2026-08-21"})
        assert "mcfinex" not in freshness(store)
