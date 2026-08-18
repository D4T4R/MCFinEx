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
    def test_current_ratio_is_always_unknown(self):
        # Screener has no current/non-current split; never guess it.
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
