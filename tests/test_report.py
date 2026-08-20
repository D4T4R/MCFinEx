"""The bulk screening path.

A full screen used to issue 43,398 queries -- one per company per label. That
is unnoticeable against a local file and eleven minutes of round-trips against
a hosted database, so the whole universe is now loaded in a handful of queries
and assembled in memory. These tests hold that property and check the two paths
agree.
"""

from __future__ import annotations

from dataclasses import asdict

import pytest

from mcfinex.db.store import Store
from mcfinex.report import metrics_for, screen_all, sector_pe_medians


class CountingConnection:
    def __init__(self, conn):
        self._conn = conn
        self.queries = 0

    def execute(self, *args, **kwargs):
        self.queries += 1
        return self._conn.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "r.db") as s:
        s.create_schema()
        for index, ticker in enumerate(["AAA", "BBB", "CCC"]):
            s.upsert_company(ticker, {
                "name": f"{ticker} Ltd", "current_price": 100.0 + index,
                "market_cap": 1000.0, "industry": "Widgets", "roce": 20.0 + index,
                "dividend_yield": 2.0, "book_value": 50.0, "stock_pe": 12.0,
                "outstanding_shares": 10.0, "last_updated": "2026-01-01",
            })
            s.replace_financials(ticker, [
                ("2025-03-31", "balance-sheet", "Reserves", 900.0),
                ("2024-03-31", "balance-sheet", "Reserves", 800.0),
                ("2025-03-31", "balance-sheet", "Equity Capital", 100.0),
                ("2025-03-31", "balance-sheet", "Other Liabilities", 50.0),
                ("2025-03-31", "balance-sheet", "Borrowings", 25.0),
                ("2025-03-31", "shareholding", "Promoters", 60.0),
                ("2024-03-31", "ratios", "Inventory Days", 100.0),
                ("2025-03-31", "ratios", "Inventory Days", 80.0),
                *[(f"202{4 + i // 4}-{(i % 4) * 3 + 3:02d}-28", "quarters", "EPS in Rs", 2.0 + i)
                  for i in range(8)],
            ])
            s.replace_valuations(ticker, "ev_ebitda", {
                "target_price": 200.0, "difference_pct": 60.0,
                "entry_price_1by4": 150.0, "entry_price_1by3": 132.0,
            })
            s.replace_valuations(ticker, "eps_yearly", {"target_price": 180.0})
            s.replace_valuations(ticker, "eps_quarterly", {"target_price": 170.0})
            s.replace_valuations(ticker, "derived", {"free_cash_flow": 10.0})
        yield s


class TestQueryCount:
    def test_a_full_screen_is_a_handful_of_queries(self, store):
        store.conn = CountingConnection(store.conn)
        screen_all(store)
        assert store.conn.queries <= 6

    def test_query_count_does_not_grow_with_the_universe(self, store):
        store.conn = CountingConnection(store.conn)
        screen_all(store)
        for_three = store.conn.queries
        for ticker in [f"X{i}" for i in range(20)]:
            store.upsert_company(ticker, {"name": ticker, "current_price": 10.0,
                                          "last_updated": "2026-01-01"})
        store.conn.queries = 0
        screen_all(store)
        assert store.conn.queries <= for_three


class TestAgreement:
    def test_bulk_metrics_match_the_per_company_path(self, store):
        medians = sector_pe_medians(store)
        for row in screen_all(store):
            single = metrics_for(store, row.screening.ticker, medians)
            assert asdict(single) == asdict(row.metrics), row.screening.ticker

    def test_subset_is_honoured(self, store):
        rows = screen_all(store, ["AAA"])
        assert [r.screening.ticker for r in rows] == ["AAA"]

    def test_unknown_ticker_is_skipped_not_fatal(self, store):
        assert screen_all(store, ["NOPE"]) == []

    def test_inventory_prior_year_survives_the_bulk_load(self, store):
        row = screen_all(store, ["AAA"])[0]
        assert row.metrics.inventory_days == 80.0
        assert row.metrics.inventory_days_prior == 100.0

    def test_quarters_reported_counts_the_series(self, store):
        assert screen_all(store, ["AAA"])[0].metrics.quarters_reported == 8


class TestBulkReaders:
    def test_all_companies_excludes_unscraped(self, store):
        store.upsert_company("SEEDED", {"isin": "IN1"})
        assert "SEEDED" not in store.all_companies()

    def test_all_series_is_newest_first(self, store):
        series = store.all_series({"balance-sheet": ["Reserves"]})
        assert series["AAA"][("balance-sheet", "Reserves")] == [900.0, 800.0]

    def test_all_valuations_nests_by_model(self, store):
        models = store.all_valuations()
        assert models["AAA"]["ev_ebitda"]["target_price"] == 200.0
