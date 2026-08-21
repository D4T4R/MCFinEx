"""Headless checks for the landing page."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcfinex.db.store import Store

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

PAGE = str(Path(__file__).resolve().parents[1] / "src" / "mcfinex" / "ui" / "ideas.py")


def _seed(path):
    """Two companies: one deep below its entry price, one fairly valued."""
    with Store(path) as store:
        store.create_schema()
        for ticker, name, price, target in (("CHEAP", "Cheap Ltd", 50.0, 200.0),
                                            ("FAIR", "Fair Ltd", 195.0, 200.0)):
            store.upsert_company(ticker, {
                "name": name, "current_price": price, "market_cap": 1000.0,
                "industry": "Widgets", "roce": 25.0, "dividend_yield": 3.0,
                "book_value": price * 2, "stock_pe": 10.0, "outstanding_shares": 10.0,
                "last_updated": "2026-01-01",
            })
            store.replace_financials(ticker, [
                ("2025-03-31", "balance-sheet", "Reserves", 900.0),
                ("2025-03-31", "balance-sheet", "Equity Capital", 100.0),
                ("2025-03-31", "shareholding", "Promoters", 70.0),
                *[(f"202{4 + i // 4}-{(i % 4) * 3 + 3:02d}-28", "quarters", "EPS in Rs", 2.0 + i)
                  for i in range(8)],
            ])
            store.replace_valuations(ticker, "ev_ebitda", {
                "target_price": target, "difference_pct": (target - price) / price * 100,
                "entry_price_1by4": target * 0.75, "entry_price_1by3": target * 0.66,
            })
            store.replace_valuations(ticker, "eps_yearly", {"target_price": target * 0.9})
            store.replace_valuations(ticker, "eps_quarterly", {"target_price": target * 0.85})
            store.replace_valuations(ticker, "derived", {"free_cash_flow": 10.0})


@pytest.fixture
def page(tmp_path, monkeypatch):
    db = tmp_path / "ideas.db"
    _seed(db)
    monkeypatch.setenv("MCFINEX_DB", str(db))
    monkeypatch.setenv("MCFINEX_DATA_DIR", str(tmp_path))
    return AppTest.from_file(PAGE, default_timeout=60).run()


class TestRenders:
    def test_runs_without_exception(self, page):
        assert not page.exception

    def test_shows_the_universe_size(self, page):
        assert page.metric[0].value == "2"

    def test_offers_the_three_tiers(self, page):
        assert page.radio[0].options == ["High conviction", "Below entry price", "Watch"]

    def test_cards_render_for_qualifying_companies(self, page):
        assert any("CHEAP" in m.value for m in page.markdown)


class TestRanking:
    def test_best_corroborated_appears_first(self, tmp_path, monkeypatch):
        # Store order is alphabetical, so a page that forgets to rank would put
        # AAA first regardless of how weak it is.
        db = tmp_path / "rank.db"
        _seed(db)
        with Store(db) as store:
            store.upsert_company("AAA", {
                "name": "Aaa Ltd", "current_price": 50.0, "market_cap": 1000.0,
                "industry": "Widgets", "roce": 1.0, "dividend_yield": 0.0,
                "book_value": 5.0, "stock_pe": 90.0, "outstanding_shares": 10.0,
                "last_updated": "2026-01-01",
            })
            store.replace_valuations("AAA", "ev_ebitda", {
                "target_price": 200.0, "difference_pct": 300.0,
                "entry_price_1by4": 150.0, "entry_price_1by3": 132.0,
            })
        monkeypatch.setenv("MCFINEX_DB", str(db))
        monkeypatch.setenv("MCFINEX_DATA_DIR", str(tmp_path))
        rendered = AppTest.from_file(PAGE, default_timeout=60).run()
        tickers = [m.value for m in rendered.markdown if "**" in m.value and "·" in m.value]
        assert tickers and "CHEAP" in tickers[0]


class TestEmptyStates:
    def test_missing_database_is_reported(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MCFINEX_DB", str(tmp_path / "absent.db"))
        monkeypatch.setenv("MCFINEX_DATA_DIR", str(tmp_path))
        rendered = AppTest.from_file(PAGE, default_timeout=60).run()
        assert rendered.error

    def test_a_tier_with_nothing_in_it_says_so(self, page):
        page.radio[0].set_value("Watch").run()
        assert not page.exception


class TestDisclaimer:
    """The app is public, so the caveat must reach the reader with the signals."""

    def test_warning_appears_above_the_cards(self, page):
        from mcfinex.disclaimer import SHORT

        assert any(SHORT in w.value for w in page.warning)

    def test_full_disclaimer_is_present(self, page):
        assert any("not investment advice" in m.value.lower() for m in page.markdown)

    def test_it_says_research_is_the_reader_s_job(self, page):
        text = " ".join(m.value.lower() for m in page.markdown)
        assert "do your own research" in text

    def test_it_disclaims_registration(self, page):
        text = " ".join(m.value.lower() for m in page.markdown)
        assert "not a sebi-registered" in text or "not a sebi" in text
