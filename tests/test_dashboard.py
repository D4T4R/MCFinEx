"""Headless checks for the Streamlit app via Streamlit's own AppTest harness.

Runs the real script and inspects the rendered element tree, so a broken widget
or a bad column reference fails here rather than in the browser.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcfinex.db.store import Store

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

# AppTest resolves relative paths against the calling file, so use the real one.
APP = str(Path(__file__).resolve().parents[1] / "src" / "mcfinex" / "ui" / "dashboard.py")


def _seed(path):
    with Store(path) as s:
        s.create_schema()
        for ticker, name, pe, price, roce in (
            ("ACME", "Acme Ltd", 12.0, 100.0, 22.0),
            ("BETA", "Beta Ltd", 40.0, 250.0, 5.0),
            ("GAMMA", "Gamma Ltd", 18.0, 75.0, 15.0),
        ):
            s.upsert_company(ticker, {
                "name": name, "stock_pe": pe, "current_price": price, "roce": roce,
                "book_value": price / 2, "dividend_yield": 2.0, "industry": "Widgets",
                "last_updated": "2026-01-01", "outstanding_shares": 10.0,
            })
            s.replace_financials(ticker, [
                ("2025-03-31", "balance-sheet", "Reserves", 900.0),
                ("2025-03-31", "balance-sheet", "Equity Capital", 100.0),
                ("2025-03-31", "balance-sheet", "Other Liabilities", 200.0),
                ("2025-03-31", "balance-sheet", "Borrowings", 100.0),
                ("2025-03-31", "shareholding", "Promoters", 60.0),
                ("2024-03-31", "ratios", "Inventory Days", 100.0),
                ("2025-03-31", "ratios", "Inventory Days", 80.0),
            ])
            s.replace_valuations(ticker, "ev_ebitda", {
                "target_price": price * 1.3, "difference_pct": 30.0,
                "entry_price_1by4": price, "entry_price_1by3": price * 0.9,
            })
            s.replace_valuations(ticker, "eps_yearly",
                                 {"target_price": price * 1.1, "difference_in_pe_pct": 9.0})
            s.replace_valuations(ticker, "eps_quarterly",
                                 {"target_price": price, "difference_in_pe_pct": 1.0})
            s.replace_valuations(ticker, "derived", {"free_cash_flow": 50.0})


@pytest.fixture
def app(tmp_path, monkeypatch):
    db = tmp_path / "dash.db"
    _seed(db)
    monkeypatch.setenv("MCFINEX_DB", str(db))
    monkeypatch.setenv("MCFINEX_DATA_DIR", str(tmp_path))
    at = AppTest.from_file(APP, default_timeout=60)
    return at.run()


class TestRenders:
    def test_app_runs_without_exception(self, app):
        assert not app.exception

    def test_title_is_shown(self, app):
        assert "MCFinEx Screener" in app.title[0].value

    def test_every_company_is_listed(self, app):
        assert app.metric[0].value == "3"

    def test_screen_table_is_rendered(self, app):
        assert len(app.dataframe) >= 1

    def test_download_button_offered(self, app):
        assert app.button  # refresh + download render as buttons

    def test_numeric_columns_render_as_numbers_not_format_strings(self, app):
        # Styler.format wants str.format specs; a printf spec like "%.2f" is
        # emitted verbatim, so every price displayed as the text "%.2f".
        from mcfinex.ui import dashboard

        for column, spec in dashboard.NUMERIC_FORMATS.items():
            assert "%" not in spec, f"{column} uses a printf spec"
            assert spec.format(1234.5678)


class TestFilters:
    def test_minimum_buy_slider_narrows_the_list(self, app):
        before = int(app.metric[1].value)
        app.sidebar.slider[0].set_value(8).run()
        assert int(app.metric[1].value) <= before

    def test_search_filters_to_one_company(self, app):
        app.sidebar.text_input[0].input("acme").run()
        assert app.metric[1].value == "1"

    def test_search_with_no_hit_is_handled(self, app):
        app.sidebar.text_input[0].input("nosuchcompany").run()
        assert not app.exception
        assert app.metric[1].value == "0"

    def test_sector_filter(self, app):
        app.sidebar.multiselect[0].select("Widgets").run()
        assert app.metric[1].value == "3"


class TestDrilldown:
    def test_detail_renders_for_the_selected_company(self, app):
        assert app.selectbox
        app.selectbox[0].select("BETA").run()
        assert not app.exception
        assert any("Beta Ltd" in m.value for m in app.markdown)

    def test_signal_tables_are_present(self, app):
        # Fundamentals and valuation tables, plus the screen and entry prices.
        assert len(app.dataframe) >= 3


class TestEmptyDatabase:
    def test_unscraped_database_shows_guidance_not_a_crash(self, tmp_path, monkeypatch):
        db = tmp_path / "empty.db"
        with Store(db) as s:
            s.create_schema()
        monkeypatch.setenv("MCFINEX_DB", str(db))
        monkeypatch.setenv("MCFINEX_DATA_DIR", str(tmp_path))
        at = AppTest.from_file(APP, default_timeout=60).run()
        assert not at.exception
        assert at.warning
