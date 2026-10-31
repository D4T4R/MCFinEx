from datetime import date
from pathlib import Path

import pytest

from mcfinex.sources import screener
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


class TestScripCodeFallback:
    """Reaching a company screener does not address by symbol.

    screener.in uses the NSE symbol where a company has one and the BSE scrip
    code where it does not. Verified against the live site: /company/NSE/ is a
    404 while /company/544937/ is National Stock Exchange of India Ltd. Without
    the fallback a BSE-only company is seeded and priced every night and never
    scraped -- and unscraped means unscreened, unpublished, and absent from the
    app with nothing anywhere reporting a problem.
    """

    def _session(self, available):
        class Response:
            def __init__(self, path):
                self.status_code = 200 if path in available else 404
                self.text = available.get(path, "")
                self.headers = {}

            def raise_for_status(self):
                pass

        class Session:
            def __init__(self):
                self.tried = []

            def get(self, url, **kwargs):
                path = url.replace(screener.BASE_URL, "")
                self.tried.append(path)
                return Response(path)

        return Session()

    def test_the_symbol_is_tried_first(self):
        sess = self._session({"/company/ACME/": "<html>acme</html>"})
        html = screener.fetch("ACME", session=sess, delay=0, alias=999)
        assert html == "<html>acme</html>"
        assert sess.tried == ["/company/ACME/"]

    def test_the_scrip_code_is_tried_when_the_symbol_404s(self):
        sess = self._session({"/company/544937/": "<html>nse ltd</html>"})
        html = screener.fetch("NSE", session=sess, delay=0, alias=544937)
        assert html == "<html>nse ltd</html>"
        assert sess.tried == ["/company/NSE/", "/company/544937/"]

    def test_without_an_alias_a_404_is_still_an_error(self):
        sess = self._session({})
        with pytest.raises(screener.ScreenerError, match="no such company"):
            screener.fetch("NSE", session=sess, delay=0)
        assert sess.tried == ["/company/NSE/"]

    def test_both_paths_failing_reports_the_symbol_not_the_code(self):
        # The ticker is what the operator asked for and what everything else
        # keys on; the scrip code would be a puzzle in a log line.
        sess = self._session({})
        with pytest.raises(screener.ScreenerError, match="NSE: no such company"):
            screener.fetch("NSE", session=sess, delay=0, alias=544937)
        assert sess.tried == ["/company/NSE/", "/company/544937/"]

    def test_consolidated_applies_to_the_fallback_too(self):
        sess = self._session({"/company/544937/consolidated/": "<html>c</html>"})
        screener.fetch("NSE", session=sess, delay=0, alias=544937, consolidated=True)
        assert sess.tried == ["/company/NSE/consolidated/",
                              "/company/544937/consolidated/"]

    def test_an_empty_alias_is_not_tried(self):
        sess = self._session({})
        with pytest.raises(screener.ScreenerError):
            screener.fetch("NSE", session=sess, delay=0, alias=None)
        assert sess.tried == ["/company/NSE/"]


class TestSameCompany:
    """Verifying that a page belongs to the company we asked for.

    Asking screener for a symbol makes it resolve that symbol, so identity comes
    free. Asking for a scrip code gets whatever company holds that number, and a
    wrong number returns a perfectly valid page for someone else. Persisting that
    files one company's financials under another's ticker, where nothing
    downstream can detect it: the row looks ordinary and the screen is simply
    wrong.

    Found by seeding five invented scrip codes and watching A1L quietly acquire
    Bikaji Foods' accounts.
    """

    def test_the_real_mismatch_is_caught(self):
        assert not screener.same_company("A-1 LIMITED", "Bikaji Foods International Ltd")

    def test_bse_name_truncation_is_tolerated(self):
        # BSE cuts names at thirty characters, so this is the normal case for a
        # long name, not an edge one.
        assert screener.same_company("National Stock Exchange of Ind",
                                     "National Stock Exchange Of India Ltd")

    def test_differing_legal_suffixes_are_tolerated(self):
        assert screener.same_company("Active Clothing Co  Limited", "Active Clothing Co Ltd")
        assert screener.same_company("AEGIS LOGISTICS LTD.", "Aegis Logistics Ltd")

    def test_punctuation_and_case_do_not_matter(self):
        assert screener.same_company("M&M LTD", "m and m ltd")

    def test_a_missing_name_does_not_block(self):
        # Absence is not evidence of a mismatch, and refusing on it would block
        # every company whose name was never seeded.
        assert screener.same_company(None, "Anything Ltd")
        assert screener.same_company("Anything Ltd", None)
        assert screener.same_company("", "")

    def test_a_name_of_only_noise_words_does_not_block(self):
        # Nothing distinguishing to compare, so there is nothing to refuse on.
        assert screener.same_company("The India Company Limited", "Reliance Industries Ltd")

    def test_two_genuinely_different_companies_are_rejected(self):
        assert not screener.same_company("Tata Steel Ltd", "Reliance Industries Ltd")
        assert not screener.same_company("Achyut Healthcare Limited", "Ambo Agritec Ltd")

    def test_one_shared_distinguishing_word_is_enough(self):
        assert screener.same_company("Ashapuri Gold Ornament Limited", "Ashapuri Gold Ltd")


class TestConsolidatedPreference:
    """Which set of accounts actually describes the business.

    A holding company's standalone accounts describe the holding company and not
    what it holds. Jio Platforms screened at a P/E of 942 on standalone earnings
    of Rs 1,281 crore against a market capitalisation of Rs 13 lakh crore;
    consolidated earnings of Rs 30,049 crore give 42. Reliance, Bajaj Finserv
    and L&T were wrong the same way by 1.8x, 4.6x and 1.2x.

    The guard matters more than the preference. Screener keeps a consolidated
    page long after a company stops filing one -- Advani Hotels last did in 2010
    -- so preferring it unconditionally replaces this year's figures with
    sixteen-year-old ones, and nothing downstream could tell.
    """

    def _company(self, *, quarters, consolidated=False):
        from mcfinex.sources.screener import Company, Section

        return Company(ticker="X", consolidated=consolidated,
                       sections={"quarters": Section(periods=list(quarters))})

    def test_current_consolidated_wins(self):
        standalone = self._company(quarters=[date(2026, 6, 30), date(2026, 9, 30)])
        group = self._company(quarters=[date(2026, 6, 30), date(2026, 9, 30)],
                              consolidated=True)
        assert screener.prefer(standalone, group).consolidated is True

    def test_consolidated_further_ahead_still_wins(self):
        standalone = self._company(quarters=[date(2026, 6, 30)])
        group = self._company(quarters=[date(2026, 9, 30)], consolidated=True)
        assert screener.prefer(standalone, group).consolidated is True

    def test_stale_consolidated_is_refused(self):
        # The Advani Hotels case: consolidated stops at 2010, standalone is
        # current. Taking the group view here loses sixteen years.
        standalone = self._company(quarters=[date(2026, 9, 30)])
        group = self._company(quarters=[date(2010, 3, 31)], consolidated=True)
        assert screener.prefer(standalone, group).consolidated is False

    def test_a_consolidated_page_with_no_quarters_is_refused(self):
        standalone = self._company(quarters=[date(2026, 9, 30)])
        group = self._company(quarters=[], consolidated=True)
        assert screener.prefer(standalone, group).consolidated is False

    def test_no_consolidated_at_all_keeps_standalone(self):
        standalone = self._company(quarters=[date(2026, 9, 30)])
        assert screener.prefer(standalone, None) is standalone

    def test_standalone_with_no_quarters_does_not_block_consolidated(self):
        # Nothing to compare against is not evidence the group view is stale,
        # and a company whose standalone page has no quarters is exactly the one
        # that needs the consolidated figures.
        standalone = self._company(quarters=[])
        group = self._company(quarters=[date(2026, 9, 30)], consolidated=True)
        assert screener.prefer(standalone, group).consolidated is True


class TestOffersConsolidated:
    """Whether to spend a second request at all.

    Asking for /consolidated/ on a company without subsidiaries returns a page
    the parser cannot read -- AMBIKCO raises IndexError on it -- so the link is
    checked rather than the request attempted and the failure caught.
    """

    def test_a_page_that_links_to_it(self):
        assert screener.offers_consolidated(
            '<a href="/company/JPL/consolidated/#balance-sheet">Consolidated</a>')

    def test_a_page_that_does_not(self):
        assert not screener.offers_consolidated(
            '<a href="/company/AMBIKCO/#balance-sheet">Balance sheet</a>')

    def test_an_empty_page(self):
        assert not screener.offers_consolidated("")

    def test_the_word_alone_is_not_enough(self):
        # "Consolidated" appears in prose on plenty of pages; only the link says
        # the view exists.
        assert not screener.offers_consolidated(
            "<p>These are the consolidated results of the group.</p>")
