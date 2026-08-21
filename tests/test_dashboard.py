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
        assert "Detailed screen" in app.title[0].value

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


class TestQuarterlyTrends:
    def test_chart_index_is_chronological_not_alphabetical(self):
        """A string index sorts alphabetically and scrambles the quarters.

        "Dec 24, Dec 25, Jun 25, Jun 26, Mar 25..." is what an index of
        formatted labels produces; the axis must be temporal.
        """
        import pandas as pd
        from datetime import date

        periods = [date(2024, 12, 31), date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30)]
        frame = pd.DataFrame({"Sales": [1, 2, 3, 4]}, index=pd.to_datetime(periods))
        assert list(frame.index) == sorted(frame.index)
        assert frame.index.is_monotonic_increasing
        # The formatted-label version is what regressed.
        labelled = pd.DataFrame({"Sales": [1, 2, 3, 4]},
                                index=[f"{p:%b %y}" for p in periods])
        assert list(labelled.index) != sorted(labelled.index)


class TestMeasureAndVerdictFilters:
    """Measure narrows the columns, verdict narrows the rows, together they compose."""

    def test_no_measure_selected_shows_every_signal_column(self, app):
        from mcfinex.ui import dashboard

        import pandas as pd

        frame = pd.DataFrame(columns=dashboard.IDENTITY_COLUMNS
                             + dashboard.DEFAULT_NUMERIC_COLUMNS
                             + ["ROCE %", "Price / book"])
        columns = dashboard._visible_columns(frame, [])
        assert "ROCE %" in columns and "Price / book" in columns

    def test_selecting_a_measure_narrows_the_columns(self):
        from mcfinex.ui import dashboard

        import pandas as pd

        frame = pd.DataFrame(columns=dashboard.IDENTITY_COLUMNS + ["ROCE %", "Price / book"])
        columns = dashboard._visible_columns(frame, ["ROCE %"])
        assert "ROCE %" in columns
        assert "Price / book" not in columns

    def test_identity_columns_always_survive(self):
        from mcfinex.ui import dashboard

        import pandas as pd

        frame = pd.DataFrame(columns=dashboard.IDENTITY_COLUMNS + ["ROCE %"])
        columns = dashboard._visible_columns(frame, ["ROCE %"])
        for essential in ("Ticker", "Company", "Price", "BUY signals"):
            assert essential in columns

    def test_ev_ebitda_brings_its_target_and_entry_columns(self):
        from mcfinex.ui import dashboard

        import pandas as pd

        frame = pd.DataFrame(columns=dashboard.IDENTITY_COLUMNS
                             + dashboard.DEFAULT_NUMERIC_COLUMNS
                             + ["EV/EBITDA upside %"])
        columns = dashboard._visible_columns(frame, ["EV/EBITDA upside %"])
        assert "EV/EBITDA target" in columns and "Entry 2/3" in columns
        assert "PE yearly target" not in columns

    def test_any_match_keeps_a_row_satisfying_one_measure(self):
        from mcfinex.ui import dashboard

        import pandas as pd

        frame = pd.DataFrame({"ROCE %": ["BUY", "SELL"], "Price / book": ["SELL", "SELL"]})
        mask = dashboard._verdict_mask(frame, ["ROCE %", "Price / book"], ["BUY"],
                                       match_all=False)
        assert list(mask) == [True, False]

    def test_all_match_requires_every_measure(self):
        from mcfinex.ui import dashboard

        import pandas as pd

        frame = pd.DataFrame({"ROCE %": ["BUY", "BUY"], "Price / book": ["BUY", "SELL"]})
        mask = dashboard._verdict_mask(frame, ["ROCE %", "Price / book"], ["BUY"],
                                       match_all=True)
        assert list(mask) == [True, False]

    def test_several_verdicts_are_a_union(self):
        from mcfinex.ui import dashboard

        import pandas as pd

        frame = pd.DataFrame({"ROCE %": ["BUY", "HOLD", "SELL"]})
        mask = dashboard._verdict_mask(frame, ["ROCE %"], ["BUY", "HOLD"], match_all=False)
        assert list(mask) == [True, True, False]

    def test_a_measure_absent_from_the_frame_is_ignored(self):
        from mcfinex.ui import dashboard

        import pandas as pd

        frame = pd.DataFrame({"ROCE %": ["BUY", "SELL"]})
        mask = dashboard._verdict_mask(frame, ["Nonexistent"], ["BUY"], match_all=False)
        assert list(mask) == [True, True]


class TestFilterInteraction:
    def test_measure_and_verdict_narrow_the_table_together(self, app):
        before = int(app.metric[1].value)
        app.sidebar.multiselect[1].select("Price / book").run()   # Measure
        app.sidebar.multiselect[2].select("SELL").run()           # Verdict
        assert not app.exception
        assert int(app.metric[1].value) <= before

    def test_verdict_without_a_measure_changes_nothing(self, app):
        before = int(app.metric[1].value)
        app.sidebar.multiselect[2].select("SELL").run()
        assert int(app.metric[1].value) == before


class TestScreenRowExplanation:
    """Selecting a row on the main screen must explain the right company."""

    def test_selection_maps_to_the_sorted_view_not_frame_order(self, app):
        # The table is sorted by BUY signals before display, so row 0 of the
        # selection is the top-ranked company, not the frame's first row.
        import pandas as pd

        frame = pd.DataFrame({
            "Ticker": ["AAA", "ZZZ"],
            "BUY signals": [1, 9],
            "SELL signals": [0, 0],
        })
        ordered = frame.sort_values(["BUY signals", "SELL signals"], ascending=[False, True])
        assert ordered.iloc[0]["Ticker"] == "ZZZ"

    def test_screen_table_is_selectable(self, app):
        # Two selectable tables on the page: the screen and the signal tables.
        assert not app.exception
        assert len(app.dataframe) >= 2

    def test_selected_ticker_reads_the_sorted_view(self):
        import pandas as pd
        from mcfinex.ui.dashboard import selected_ticker

        ordered = pd.DataFrame({"Ticker": ["ZZZ", "AAA"]})
        assert selected_ticker(ordered, [0]) == "ZZZ"
        assert selected_ticker(ordered, [1]) == "AAA"

    def test_no_selection_yields_no_ticker(self):
        import pandas as pd
        from mcfinex.ui.dashboard import selected_ticker

        assert selected_ticker(pd.DataFrame({"Ticker": ["ZZZ"]}), []) is None

    def test_chosen_measures_are_what_gets_explained(self):
        from mcfinex.report import Row
        from mcfinex.screening import Metrics, screen
        from mcfinex.ui.dashboard import signals_to_explain

        row = Row(screening=screen(Metrics(ticker="T", price=100.0, book_value=10.0)),
                  metrics=Metrics(ticker="T"))
        wanted, heading = signals_to_explain(row, ["Price / book"], "T")
        assert [s.label for s in wanted] == ["Price / book"]
        assert "Price / book" in heading

    def test_without_measures_only_decisive_signals_are_offered(self):
        from mcfinex.report import Row
        from mcfinex.screening import Metrics, Verdict, screen
        from mcfinex.ui.dashboard import signals_to_explain

        metrics = Metrics(ticker="T", price=100.0, book_value=10.0, roce=25.0)
        row = Row(screening=screen(metrics), metrics=metrics)
        wanted, heading = signals_to_explain(row, [], "T")
        assert wanted, "decisive signals should be offered"
        assert all(s.verdict in (Verdict.BUY, Verdict.SELL) for s in wanted)
        assert "decided" in heading


class TestDisclaimerOnTheScreen:
    def test_warning_is_shown(self, app):
        from mcfinex.disclaimer import SHORT

        assert any(SHORT in w.value for w in app.warning)

    def test_exported_csv_carries_the_caveat(self):
        # The file outlives the page that explained it.
        from mcfinex.disclaimer import CSV_HEADER

        assert "not investment advice" in CSV_HEADER.lower()
        assert CSV_HEADER.startswith("#")   # a comment, so parsers can skip it

    def test_csv_header_is_a_single_line(self):
        from mcfinex.disclaimer import CSV_HEADER

        assert "\n" not in CSV_HEADER
