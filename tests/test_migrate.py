"""Copying a screened database to another store.

Exercised between two SQLite databases so it runs without a network; the same
code path drives the Postgres push, since Store hides the dialect.
"""

from __future__ import annotations

import pytest

from mcfinex.db.store import Store
from mcfinex.migrate import compare, migrate


def seed(path, *, scraped=("AAA", "BBB"), seeded=("CCC",)):
    store = Store(path)
    store.create_schema()
    for ticker in scraped:
        store.upsert_company(ticker, {
            "name": f"{ticker} Ltd", "current_price": 100.0, "industry": "Widgets",
            "last_updated": "2026-01-01", "roce": 20.0,
        })
        store.replace_financials(ticker, [
            ("2025-03-31", "balance-sheet", "Reserves", 900.0),
            ("2025-03-31", "quarters", "EPS in Rs", 5.0),
        ])
        store.replace_valuations(ticker, "ev_ebitda", {"target_price": 200.0})
    for ticker in seeded:
        # Seeded from the bhavcopy but never scraped: a price and nothing else.
        store.upsert_company(ticker, {"isin": "IN123", "current_price": 50.0})
    return store


@pytest.fixture
def source(tmp_path):
    store = seed(tmp_path / "src.db")
    yield store
    store.close()


@pytest.fixture
def target(tmp_path):
    store = Store(tmp_path / "dst.db")
    yield store
    store.close()


class TestMigrate:
    def test_copies_every_table(self, source, target):
        result = migrate(source, target)
        assert result.copied["companies"] == 2
        assert result.copied["financials"] == 4
        assert result.copied["valuations"] == 2

    def test_unscraped_companies_are_left_behind(self, source, target):
        # CCC has a price and no financials; the deployed app cannot screen it.
        migrate(source, target)
        assert target.company("CCC") is None
        assert target.company("AAA") is not None

    def test_all_includes_the_unscraped(self, source, target):
        migrate(source, target, only_screened=False)
        assert target.company("CCC") is not None

    def test_values_survive_the_round_trip(self, source, target):
        migrate(source, target)
        assert target.company("AAA")["name"] == "AAA Ltd"
        assert target.series("AAA", "balance-sheet", "Reserves") == [900.0]
        assert target.valuation_fields("AAA", "ev_ebitda") == {"target_price": 200.0}

    def test_the_target_is_replaced_not_merged(self, source, target):
        target.create_schema()
        target.upsert_company("STALE", {"name": "Stale Ltd", "last_updated": "2020-01-01"})
        migrate(source, target)
        # A leftover from a previous push would have no clear as-of date.
        assert target.company("STALE") is None

    def test_migrating_twice_does_not_duplicate(self, source, target):
        migrate(source, target)
        first = compare(source, target)["financials"][1]
        migrate(source, target)
        assert compare(source, target)["financials"][1] == first

    def test_an_empty_source_is_survivable(self, tmp_path, target):
        empty = Store(tmp_path / "empty.db")
        empty.create_schema()
        result = migrate(empty, target)
        assert result.total == 0
        empty.close()


class TestCompare:
    def test_reports_both_sides(self, source, target):
        migrate(source, target)
        counts = compare(source, target)
        # The source keeps its unscraped company, the target does not.
        assert counts["companies"] == (3, 2)
        assert counts["financials"][0] == counts["financials"][1]

    def test_covers_every_migrated_table(self, source, target):
        migrate(source, target)
        assert set(compare(source, target)) == {
            "companies", "financials", "valuations", "result_calendar",
        }
