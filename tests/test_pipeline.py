import pytest

from mcfinex.db.store import Store
from mcfinex.pipeline import revalue


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "p.db") as s:
        s.create_schema()
        s.upsert_company("ACME", {
            "name": "Acme Ltd", "current_price": 100.0,
            "outstanding_shares": 10.0, "last_updated": "2026-01-01",
        })
        s.replace_financials("ACME", [
            ("2023-03-31", "profit-loss", "Operating Profit", 100.0),
            ("2024-03-31", "profit-loss", "Operating Profit", 110.0),
            ("2025-03-31", "profit-loss", "Operating Profit", 121.0),
            ("2023-03-31", "profit-loss", "EPS in Rs", 8.0),
            ("2024-03-31", "profit-loss", "EPS in Rs", 9.0),
            ("2025-03-31", "profit-loss", "EPS in Rs", 10.0),
            ("2025-03-31", "balance-sheet", "Borrowings", 50.0),
        ])
        s.replace_valuations("ACME", "derived", {"ev_ebitda_multiple": 8.0, "ebit": 42.0})
        yield s


class TestRevalue:
    def test_recomputes_from_stored_facts_without_network(self, store):
        assert revalue(store, "ACME") is True
        assert store.valuation_fields("ACME", "eps_yearly")["current_pe"] == pytest.approx(10.0)

    def test_current_pe_tracks_a_price_change(self, store):
        revalue(store, "ACME")
        before = store.valuation_fields("ACME", "eps_yearly")["current_pe"]
        store.update_prices({"ACME": 200.0}, "2026-08-18")
        revalue(store, "ACME")
        after = store.valuation_fields("ACME", "eps_yearly")["current_pe"]
        assert after == pytest.approx(before * 2)

    def test_series_order_is_restored_newest_last(self, store):
        # Stored newest-first, but the models need oldest-first or the current
        # P/E would divide by the oldest EPS -- the original Java's bug.
        revalue(store, "ACME")
        fields = store.valuation_fields("ACME", "eps_yearly")
        assert fields["current_pe"] == pytest.approx(100.0 / 10.0)

    def test_ev_multiple_is_rescaled_with_the_new_price(self, store):
        revalue(store, "ACME")
        first = store.valuation_fields("ACME", "derived")["ev_ebitda_multiple"]
        store.update_prices({"ACME": 200.0}, "2026-08-18")
        revalue(store, "ACME")
        second = store.valuation_fields("ACME", "derived")["ev_ebitda_multiple"]
        # Market cap doubled, so enterprise value and the multiple must rise.
        assert second > first

    def test_derived_fields_are_preserved(self, store):
        revalue(store, "ACME")
        assert store.valuation_fields("ACME", "derived")["ebit"] == 42.0

    def test_unknown_company_is_reported(self, store):
        assert revalue(store, "NOPE") is False

    def test_company_without_a_price_is_skipped(self, store):
        store.upsert_company("NOPRICE", {"name": "No Price"})
        assert revalue(store, "NOPRICE") is False
