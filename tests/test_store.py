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
