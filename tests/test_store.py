from datetime import date

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
