"""On-demand schedule enrichment.

Covers the behaviour that makes the feature safe to expose behind a button:
it must never run during a bulk scrape, must recompute what the new data
affects, and must leave the company untouched when screener has nothing.
"""

from __future__ import annotations

import pytest

from mcfinex.db.store import Store
from mcfinex.enrich import enrich
from mcfinex.report import metrics_for
from mcfinex.screening import Verdict, screen

SCHEDULES = {
    "Other Assets": {
        "Inventories": {"Mar 2026": "225"},
        "Trade receivables": {"Mar 2026": "71"},
        "Cash Equivalents": {"Mar 2026": "34"},
        "Loans n Advances": {"Mar 2026": "3"},
        # Nested expandables carry a JS call where a number would be.
        "Nested": {"isExpandable": 'Company.showSchedule("x", "y", this)'},
    },
    "Other Liabilities": {
        "Trade Payables": {"Mar 2026": "15"},
        "Advance from Customers": {"Mar 2026": "0"},
    },
    "Borrowings": {
        "Long term Borrowings": {"Mar 2026": "56"},
        "Short term Borrowings": {"Mar 2026": "250"},
    },
}


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "e.db") as s:
        s.create_schema()
        s.upsert_company("ACME", {
            "name": "Acme Ltd", "company_id": 999, "current_price": 100.0,
            "market_cap": 1000.0, "outstanding_shares": 10.0,
            "last_updated": "2026-01-01",
        })
        s.replace_financials("ACME", [
            ("2024-03-31", "profit-loss", "Operating Profit", 90.0),
            ("2025-03-31", "profit-loss", "Operating Profit", 100.0),
            ("2026-03-31", "profit-loss", "Operating Profit", 110.0),
            ("2026-03-31", "balance-sheet", "Borrowings", 300.0),
        ])
        s.replace_valuations("ACME", "derived", {"ev_ebitda_multiple": 11.8})
        yield s


@pytest.fixture
def fake_api(monkeypatch):
    calls = []

    def fake_fetch_schedule(company_id, parent, section="balance-sheet", **kw):
        calls.append(parent)
        return SCHEDULES.get(parent, {})

    monkeypatch.setattr("mcfinex.enrich.screener.fetch_schedule", fake_fetch_schedule)
    return calls


class TestEnrich:
    def test_rolls_up_the_components(self, store, fake_api):
        r = enrich(store, "ACME")
        assert r.cash == 34.0
        assert r.current_assets == 225 + 71 + 34 + 3
        assert r.current_liabilities == 15 + 0 + 250
        assert r.long_term_borrowings == 56.0

    def test_nested_expandables_are_skipped_not_parsed(self, store, fake_api):
        # A JS call is not a number; parsing it would poison the roll-up.
        r = enrich(store, "ACME")
        assert r.found_anything
        assert store.series("ACME", "schedule", "Nested") == []

    def test_cash_lowers_the_enterprise_value(self, store, fake_api):
        before = store.valuation_fields("ACME", "derived")["ev_ebitda_multiple"]
        enrich(store, "ACME")
        after = store.valuation_fields("ACME", "derived")
        # EV = market cap + debt - cash, so netting cash off must reduce it.
        assert after["enterprise_value"] == 1000.0 + 300.0 - 34.0
        assert after["ev_ebitda_multiple"] < before

    def test_target_price_is_recomputed(self, store, fake_api):
        enrich(store, "ACME")
        assert store.valuation_fields("ACME", "ev_ebitda").get("target_price") is not None

    def test_reports_that_it_revalued(self, store, fake_api):
        assert enrich(store, "ACME").revalued is True

    def test_current_ratio_becomes_scoreable(self, store, fake_api):
        before = screen(metrics_for(store, "ACME", {})).get("current_ratio")
        assert before.verdict is Verdict.UNKNOWN
        enrich(store, "ACME")
        after = screen(metrics_for(store, "ACME", {})).get("current_ratio")
        assert after.verdict is not Verdict.UNKNOWN
        assert after.value == pytest.approx(333 / 265)

    def test_unknown_company_is_a_no_op(self, store, fake_api):
        assert enrich(store, "NOPE").found_anything is False

    def test_no_schedules_leaves_the_company_alone(self, store, monkeypatch):
        monkeypatch.setattr("mcfinex.enrich.screener.fetch_schedule",
                            lambda *a, **k: {})
        before = store.valuation_fields("ACME", "derived")
        result = enrich(store, "ACME")
        assert not result.found_anything
        assert store.valuation_fields("ACME", "derived") == before

    def test_company_id_is_looked_up_when_missing(self, store, fake_api, monkeypatch):
        store.upsert_company("OLD", {"name": "Old Co", "current_price": 10.0,
                                     "market_cap": 50.0, "last_updated": "2026-01-01"})
        monkeypatch.setattr("mcfinex.enrich.screener.fetch", lambda *a, **k: "<html/>")

        class Parsed:
            company_id = 4242

        monkeypatch.setattr("mcfinex.enrich.screener.parse", lambda *a, **k: Parsed())
        enrich(store, "OLD")
        assert store.company("OLD")["company_id"] == 4242


class TestScheduleStorage:
    def test_schedules_do_not_disturb_the_statements(self, store, fake_api):
        enrich(store, "ACME")
        assert store.series("ACME", "profit-loss", "Operating Profit") == [110.0, 100.0, 90.0]

    def test_re_enriching_replaces_rather_than_duplicates(self, store, fake_api):
        enrich(store, "ACME")
        first = store.conn.execute(
            "SELECT COUNT(*) FROM financials WHERE ticker='ACME' AND statement='schedule'"
        ).fetchone()[0]
        enrich(store, "ACME")
        second = store.conn.execute(
            "SELECT COUNT(*) FROM financials WHERE ticker='ACME' AND statement='schedule'"
        ).fetchone()[0]
        assert first == second

    def test_a_rescrape_clears_stale_schedule_rows(self, store, fake_api):
        # Cash from a previous quarter must not sit beside fresh statements.
        enrich(store, "ACME")
        assert store.has_schedule("ACME")
        store.replace_financials("ACME", [("2027-03-31", "profit-loss", "Operating Profit", 5.0)])
        assert not store.has_schedule("ACME")
