from datetime import date

import pytest

from mcfinex.trends import Confidence, analyse

QUARTERS = [date(2024, 9, 30), date(2024, 12, 31), date(2025, 3, 31), date(2025, 6, 30),
            date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)]


def build(values, periods=None):
    return analyse("Sales", periods or QUARTERS[-len(values):], values)


class TestGrowth:
    def test_compares_like_quarter_with_like(self):
        # Strongly seasonal: Q4 always spikes. Year-on-year should read flat,
        # where quarter-on-quarter would swing wildly.
        seasonal = [100, 300, 100, 100, 100, 300, 100, 100]
        assert build(seasonal).yoy_growth == [0.0, 0.0, 0.0, 0.0]

    def test_detects_real_growth_through_seasonality(self):
        trend = build([100, 300, 100, 100, 110, 330, 110, 110])
        assert all(g == pytest.approx(10.0) for g in trend.yoy_growth)

    def test_growth_aligns_to_the_last_four_quarters(self):
        assert len(build([1, 2, 3, 4, 5, 6, 7, 8]).yoy_growth) == 4

    def test_loss_making_base_still_reads_as_improvement(self):
        # -10 to -5 is better, so growth must be positive.
        assert build([-10, 1, 1, 1, -5, 1, 1, 1]).yoy_growth[0] > 0

    def test_zero_base_is_none_not_a_crash(self):
        assert build([0, 1, 1, 1, 5, 1, 1, 1]).yoy_growth[0] is None


class TestTrailingTwelveMonths:
    def test_sums_the_last_four_against_the_prior_four(self):
        trend = build([10, 10, 10, 10, 20, 20, 20, 20])
        assert trend.ttm == 80
        assert trend.ttm_prior == 40
        assert trend.ttm_growth_pct == pytest.approx(100.0)

    def test_needs_eight_quarters(self):
        assert build([10, 10, 10, 10, 20]).ttm is None

    def test_improving_reflects_ttm_direction(self):
        assert build([20, 20, 20, 20, 10, 10, 10, 10]).improving is False
        assert build([10, 10, 10, 10, 20, 20, 20, 20]).improving is True


class TestForecast:
    def test_projects_from_the_same_quarter_last_year(self):
        # Steady 10% growth; Sep 2025 was 110, so Sep 2026 projects to 121.
        trend = build([100, 100, 100, 100, 110, 110, 110, 110])
        assert trend.forecast == pytest.approx(121.0)

    def test_labels_the_next_quarter(self):
        assert build([1, 2, 3, 4, 5, 6, 7, 8]).forecast_period == "Sep 2026"

    def test_year_boundary_rolls_over(self):
        periods = [date(2024, 3, 31), date(2024, 6, 30), date(2024, 9, 30), date(2024, 12, 31),
                   date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31)]
        assert build([1] * 8, periods).forecast_period == "Mar 2026"

    def test_steady_growth_is_high_confidence(self):
        assert build([100, 100, 100, 100, 110, 110, 110, 110]).confidence is Confidence.HIGH

    def test_erratic_growth_is_low_confidence(self):
        trend = build([100, 100, 100, 100, 300, 20, 250, 30])
        assert trend.confidence is Confidence.LOW

    def test_too_little_history_gives_no_forecast(self):
        trend = build([10, 20, 30])
        assert trend.forecast is None
        assert "year-on-year" in trend.note


class TestWindow:
    def test_only_the_last_eight_quarters_are_used(self):
        periods = [date(2020 + i // 4, (i % 4) * 3 + 3, 28) for i in range(16)]
        trend = analyse("Sales", periods, list(range(16)))
        assert len(trend.values) == 8
        assert trend.values[-1] == 15

    def test_empty_input_is_survivable(self):
        trend = analyse("Sales", [], [])
        assert trend.latest is None
        assert trend.forecast is None
