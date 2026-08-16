from datetime import date
from pathlib import Path

import pytest

from mcfinex.sources.screener import ScreenerError, parse, to_number

FIXTURE = Path(__file__).parent / "fixtures" / "coastcorp.html"


@pytest.fixture(scope="module")
def company():
    return parse(FIXTURE.read_text(), "COASTCORP")


class TestToNumber:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("1,234", 1234.0), ("12.5%", 12.5), ("₹ 41.8", 41.8),
            ("-58", -58.0), ("0", 0.0),
        ],
    )
    def test_strips_decoration(self, text, expected):
        assert to_number(text) == expected

    @pytest.mark.parametrize("text", ["", "-", "--", None, "n/a"])
    def test_blanks_become_none_never_a_sentinel(self, text):
        # The Java returned -8888888 / -9999999 here and wrote them into
        # numeric columns, which silently corrupted every downstream average.
        assert to_number(text) is None


class TestParse:
    def test_reads_the_company_name(self, company):
        assert company.name == "Coastal Corporation Ltd"

    def test_industry_breadcrumb_is_broadest_first(self, company):
        assert company.industry == ["Fast Moving Consumer Goods", "Food Products", "Seafood"]

    def test_headline_ratios(self, company):
        assert company.ratios["Current Price"] == 41.8
        assert company.ratios["Market Cap"] == 280.0
        assert company.ratios["Face Value"] == 2.0

    def test_high_low_is_split_into_two_ratios(self, company):
        assert company.ratios["High"] == 67.4
        assert company.ratios["Low"] == 29.7

    def test_all_sections_present(self, company):
        for name in ("quarters", "profit-loss", "balance-sheet", "cash-flow",
                     "ratios", "shareholding"):
            assert name in company.sections, name

    def test_rejects_a_non_company_page(self):
        with pytest.raises(ScreenerError):
            parse("<html><body>nothing here</body></html>", "NOPE")


class TestSections:
    def test_ttm_column_excluded_from_series(self, company):
        pl = company.section("profit-loss")
        # The TTM column is kept for alignment but is not a reporting period.
        assert len(pl.periods) > len(pl.dated_periods())
        assert len(pl.series("EPS in Rs")) == len(pl.dated_periods())

    def test_latest_ignores_the_ttm_column(self, company):
        pl = company.section("profit-loss")
        assert pl.latest("Operating Profit") == pl.series("Operating Profit")[-1]

    def test_label_match_tolerates_the_expand_marker(self, company):
        # Screener renders expandable rows as "Borrowings +".
        assert company.section("balance-sheet").latest("Borrowings") is not None

    def test_unknown_label_is_all_none_not_an_error(self, company):
        assert set(company.section("balance-sheet").row("Nonexistent")) == {None}

    def test_series_is_chronological(self, company):
        periods = company.section("profit-loss").dated_periods()
        assert periods == sorted(periods)

    def test_shareholding_headers_parse_without_a_date_key(self, company):
        # This table labels columns "Sep 2023" with no data-date-key attribute.
        shp = company.section("shareholding")
        assert shp.dated_periods()
        assert all(isinstance(p, date) for p in shp.dated_periods())
        assert 0 < shp.latest("Promoters") <= 100
