import pytest

from mcfinex.screening import Metrics, Verdict, screen


def verdict(key: str, **kw) -> Verdict:
    return screen(Metrics(ticker="T", **kw)).get(key).verdict


class TestPromoter:
    def test_pledge_is_preferred_when_available(self):
        assert verdict("promoter", promoter_pledge=20.0) is Verdict.SELL
        assert verdict("promoter", promoter_pledge=0.0) is Verdict.BUY

    def test_falls_back_to_holding(self):
        assert verdict("promoter", promoter_holding=60.0) is Verdict.BUY
        assert verdict("promoter", promoter_holding=10.0) is Verdict.SELL
        assert verdict("promoter", promoter_holding=40.0) is Verdict.HOLD

    def test_missing_is_unknown(self):
        assert verdict("promoter") is Verdict.UNKNOWN


class TestDebtToEquity:
    @pytest.mark.parametrize("liabilities, expected", [
        (50.0, Verdict.BUY),    # 0.5x
        (150.0, Verdict.HOLD),  # 1.5x
        (300.0, Verdict.SELL),  # 3x
    ])
    def test_lower_is_better(self, liabilities, expected):
        assert verdict("debt_to_equity", reserves=90.0, equity_capital=10.0,
                       other_liabilities=liabilities) is expected

    def test_mid_band_is_hold_not_buy(self):
        # The inverted band must not treat everything below the SELL line as BUY.
        assert verdict("debt_to_equity", reserves=90.0, equity_capital=10.0,
                       other_liabilities=180.0) is Verdict.HOLD

    def test_borrowings_count_as_debt(self):
        assert verdict("debt_to_equity", reserves=90.0, equity_capital=10.0,
                       borrowings=300.0) is Verdict.SELL

    def test_zero_equity_is_unknown(self):
        assert verdict("debt_to_equity", reserves=0.0, equity_capital=0.0,
                       borrowings=10.0) is Verdict.UNKNOWN


class TestPriceToBook:
    @pytest.mark.parametrize("price, expected", [
        (100.0, Verdict.BUY),    # 1.0x
        (200.0, Verdict.HOLD),   # 2.0x
        (400.0, Verdict.SELL),   # 4.0x
    ])
    def test_bands(self, price, expected):
        assert verdict("price_to_book", price=price, book_value=100.0) is expected


class TestPeVsSector:
    def test_cheap_against_the_sector_is_a_buy(self):
        assert verdict("pe", stock_pe=10.0, sector_pe=20.0) is Verdict.BUY

    def test_expensive_is_a_sell(self):
        assert verdict("pe", stock_pe=30.0, sector_pe=20.0) is Verdict.SELL

    def test_in_line_is_hold(self):
        assert verdict("pe", stock_pe=20.5, sector_pe=20.0) is Verdict.HOLD

    def test_loss_making_is_a_sell_not_a_bargain(self):
        # A negative P/E is not a cheap one.
        assert verdict("pe", stock_pe=-5.0, sector_pe=20.0) is Verdict.SELL

    def test_without_a_sector_median_it_is_unknown(self):
        assert verdict("pe", stock_pe=10.0) is Verdict.UNKNOWN


class TestInventory:
    def test_falling_days_is_a_buy(self):
        assert verdict("inventory", inventory_days=80.0,
                       inventory_days_prior=100.0) is Verdict.BUY

    def test_rising_days_is_a_sell(self):
        assert verdict("inventory", inventory_days=120.0,
                       inventory_days_prior=100.0) is Verdict.SELL

    def test_flat_is_hold(self):
        assert verdict("inventory", inventory_days=101.0,
                       inventory_days_prior=100.0) is Verdict.HOLD

    def test_no_inventory_is_unknown_not_sell(self):
        # Banks and service companies carry none; that is not a failure.
        assert verdict("inventory") is Verdict.UNKNOWN


class TestDividendYield:
    def test_meaningful_yield_is_a_buy(self):
        assert verdict("dividend_yield", dividend_yield=2.0) is Verdict.BUY

    def test_token_yield_is_hold(self):
        assert verdict("dividend_yield", dividend_yield=0.5) is Verdict.HOLD

    def test_no_dividend_is_a_sell(self):
        assert verdict("dividend_yield", dividend_yield=0.0) is Verdict.SELL


class TestValuationSignals:
    def test_upside_above_ten_percent_is_a_buy(self):
        assert verdict("ev_ebitda", ev_ebitda_upside=25.0) is Verdict.BUY

    def test_downside_is_a_sell(self):
        assert verdict("ev_ebitda", ev_ebitda_upside=-8.0) is Verdict.SELL

    def test_small_upside_is_hold(self):
        assert verdict("ev_ebitda", ev_ebitda_upside=5.0) is Verdict.HOLD

    def test_rerating_band(self):
        assert verdict("pe_yearly", pe_yearly_rerating=9.0) is Verdict.BUY
        assert verdict("pe_yearly", pe_yearly_rerating=0.0) is Verdict.HOLD
        assert verdict("pe_yearly", pe_yearly_rerating=-9.0) is Verdict.SELL


class TestScoring:
    def test_current_ratio_is_unknown_until_enriched(self):
        # The company page has no current/non-current split; never guess it.
        signal = screen(Metrics(ticker="T")).get("current_ratio")
        assert signal.verdict is Verdict.UNKNOWN
        assert signal.available is False

    def test_unknown_signals_do_not_count_as_buys(self):
        result = screen(Metrics(ticker="T"))
        assert result.buy_count == 0
        assert result.scored_count == 0

    def test_scored_count_is_the_denominator(self):
        result = screen(Metrics(ticker="T", roce=25.0, dividend_yield=3.0))
        assert result.buy_count == 2
        assert result.scored_count >= 2
        assert result.scored_count < len(result.fundamentals)

    def test_valuation_signals_excluded_from_buy_count(self):
        # OVERALL counted only the fundamental screens (Results!C:L).
        result = screen(Metrics(ticker="T", ev_ebitda_upside=50.0))
        assert result.buy_count == 0

    def test_signal_keys_are_unique(self):
        keys = [s.key for s in screen(Metrics(ticker="T")).signals]
        assert len(keys) == len(set(keys))


class TestNewlyListed:
    """An IPO multiplies the share count, breaking per-share comparisons."""

    def test_no_quarterly_history_means_newly_listed(self):
        assert Metrics(ticker="T", quarters_reported=0).newly_listed
        assert not Metrics(ticker="T", quarters_reported=4).newly_listed

    def test_uncounted_is_not_treated_as_newly_listed(self):
        # None means "not measured", which must not silently withhold signals.
        assert not Metrics(ticker="T").newly_listed
        assert screen(Metrics(ticker="T", pe_yearly_rerating=9.0)) \
            .get("pe_yearly").verdict is Verdict.BUY

    def test_rerating_is_withheld_for_a_new_listing(self):
        # Milky Mist's yearly EPS reads 90.71 -> 0.69 across its IPO; the model
        # would otherwise report that as a -37% re-rating.
        m = Metrics(ticker="T", quarters_reported=0, pe_yearly_rerating=-37.0,
                    pe_quarterly_rerating=12.0)
        result = screen(m)
        assert result.get("pe_yearly").verdict is Verdict.UNKNOWN
        assert result.get("pe_quarterly").verdict is Verdict.UNKNOWN
        assert "IPO" in result.get("pe_yearly").rule

    def test_established_companies_are_unaffected(self):
        m = Metrics(ticker="T", quarters_reported=8, pe_yearly_rerating=9.0)
        assert screen(m).get("pe_yearly").verdict is Verdict.BUY

    def test_withheld_signals_are_marked_unavailable(self):
        signal = screen(Metrics(ticker="T", quarters_reported=0)).get("pe_yearly")
        assert signal.available is False

    def test_ev_ebitda_still_applies(self):
        # EBITDA is an absolute figure, not per share, so the IPO does not
        # invalidate it the way it invalidates EPS.
        m = Metrics(ticker="T", quarters_reported=0, ev_ebitda_upside=30.0)
        assert screen(m).get("ev_ebitda").verdict is Verdict.BUY


class TestExplanations:
    """Every verdict must be able to show its working."""

    def full(self):
        return Metrics(
            ticker="T", price=670.05, book_value=78.4, reserves=900.0,
            equity_capital=100.0, other_liabilities=50.0, borrowings=10.0,
            roce=59.4, dividend_yield=2.0, free_cash_flow=588.0,
            stock_pe=23.8, sector_pe=29.7, promoter_holding=59.11,
            current_assets=300.0, current_liabilities=45.0,
            inventory_days=148.0, inventory_days_prior=160.0,
            quarters_reported=12, ev_ebitda_upside=86.1,
            ev_ebitda_upside_with_debt=85.8, pe_yearly_rerating=48.8,
            pe_quarterly_rerating=26.1,
        )

    def test_every_scoreable_signal_explains_itself(self):
        for signal in screen(self.full()).signals:
            if signal.available:
                assert signal.explanation is not None, signal.label

    def test_price_to_book_shows_the_arithmetic(self):
        signal = screen(self.full()).get("price_to_book")
        assert signal.verdict is Verdict.SELL
        assert signal.explanation.arithmetic(signal.value) == "670.05 / 78.40 = 8.55"

    def test_reasoning_names_the_threshold_crossed(self):
        signal = screen(self.full()).get("price_to_book")
        assert "above 3.00" in signal.explanation.reasoning
        assert "SELL" in signal.explanation.reasoning

    def test_inputs_are_the_ones_in_the_formula(self):
        explanation = screen(self.full()).get("price_to_book").explanation
        assert dict(explanation.inputs) == {"Share price": 670.05, "Book value per share": 78.4}

    def test_reference_is_a_resolvable_search_link(self):
        # Investopedia blocks automated requests, so deep term URLs cannot be
        # verified; a search link always resolves.
        url = screen(self.full()).get("price_to_book").explanation.url
        assert url.startswith("https://www.investopedia.com/search?q=")
        assert "price" in url

    def test_definitions_are_written_not_quoted(self):
        # Original prose, so nothing is reproduced from the source.
        for signal in screen(self.full()).signals:
            if signal.explanation:
                assert len(signal.explanation.definition) > 60

    def test_unavailable_signals_need_no_explanation(self):
        signal = screen(Metrics(ticker="T")).get("current_ratio")
        assert not signal.available

    def test_percentage_signals_carry_their_unit(self):
        assert "%" in screen(self.full()).get("roce").explanation.reasoning

    def test_hold_reasoning_names_both_bounds(self):
        held = Metrics(ticker="T", price=200.0, book_value=100.0)  # 2.0x
        reasoning = screen(held).get("price_to_book").explanation.reasoning
        assert "between" in reasoning and "HOLD" in reasoning
