import math

import pytest

from mcfinex.valuation import growth_series, mean, value_by_eps, value_by_ev_ebitda


class TestGrowthSeries:
    def test_growth_is_a_fraction_not_a_percentage(self):
        assert growth_series([100.0, 110.0]) == [0.1]

    def test_gaps_become_none_rather_than_zero(self):
        # A missing period is unknown, not "no growth"; averaging must skip it.
        assert growth_series([100.0, None, 121.0]) == [None, None]

    def test_zero_base_does_not_divide_by_zero(self):
        assert growth_series([0.0, 50.0]) == [None]

    def test_negative_base_keeps_improvement_positive(self):
        # Losses shrinking from -10 to -5 is an improvement, so growth is > 0.
        assert growth_series([-10.0, -5.0])[0] > 0

    def test_series_is_one_shorter_than_input(self):
        assert len(growth_series([1.0, 2.0, 3.0, 4.0, 5.0])) == 4


class TestMean:
    def test_skips_none(self):
        assert mean([1.0, None, 3.0]) == 2.0

    def test_all_missing_is_none(self):
        assert mean([None, None]) is None

    def test_ignores_non_finite(self):
        assert mean([2.0, math.inf, math.nan]) == 2.0


class TestEvEbitda:
    def make(self, **kw):
        params = dict(
            ev_ebitda_multiple=10.0,
            outstanding_shares=100.0,
            long_term_borrowings=0.0,
            current_price=50.0,
        )
        params.update(kw)
        return value_by_ev_ebitda([100.0, 110.0, 121.0], **params)

    def test_forecasts_from_the_newest_period(self):
        # 10% average growth applied to the latest 121 -> 133.1
        result = self.make()
        assert result.expected_ebitda == approx(133.1)
        assert result.forecast_ev == approx(1331.0)

    def test_borrowings_reduce_the_target_price(self):
        # Equity value is EV minus net debt. The Java computed (debt - EV),
        # which made every leveraged company come out negative.
        plain = self.make(long_term_borrowings=0.0).target_price_with_borrowing
        levered = self.make(long_term_borrowings=500.0).target_price_with_borrowing
        assert levered < plain
        assert levered > 0

    def test_entry_prices_discount_the_target(self):
        r = self.make()
        assert r.entry_price_1by4 == approx(r.target_price * 0.75)
        assert r.entry_price_1by3 == approx(r.target_price * 0.66)

    def test_missing_shares_yields_none_not_a_crash(self):
        assert self.make(outstanding_shares=None).target_price is None

    def test_empty_history_is_survivable(self):
        result = value_by_ev_ebitda(
            [], ev_ebitda_multiple=None, outstanding_shares=None,
            long_term_borrowings=None, current_price=None,
        )
        assert result.target_price is None


class TestEps:
    def test_current_pe_uses_the_newest_eps(self):
        # Series is oldest-first, so 10.0 is current: 200 / 10 = 20.
        result = value_by_eps([2.0, 5.0, 10.0], current_price=200.0)
        assert result.current_pe == approx(20.0)

    def test_growth_lifts_forward_eps_above_current(self):
        result = value_by_eps([10.0, 11.0, 12.1], current_price=100.0)
        assert result.forward_eps > 12.1

    def test_forward_pe_below_current_pe_when_growing(self):
        result = value_by_eps([10.0, 11.0, 12.1], current_price=100.0)
        assert result.forward_pe < result.current_pe
        assert result.difference_in_pe_pct > 0

    def test_zero_eps_does_not_divide_by_zero(self):
        result = value_by_eps([1.0, 0.0], current_price=100.0)
        assert result.current_pe is None
        assert result.target_price is None

    def test_missing_price_yields_no_target(self):
        assert value_by_eps([1.0, 2.0], current_price=None).target_price is None


def approx(value):
    return pytest.approx(value, rel=1e-6)
