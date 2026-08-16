from datetime import date

import pytest

from mcfinex.quarters import current_quarter, expected_reported_period, is_current


class TestCurrentQuarter:
    @pytest.mark.parametrize(
        "day, expected",
        [
            (date(2025, 4, 1), "2026-1"),   # FY starts in April
            (date(2025, 6, 30), "2026-1"),
            (date(2025, 7, 1), "2026-2"),
            (date(2025, 9, 30), "2026-2"),
            (date(2025, 10, 1), "2026-3"),
            (date(2025, 12, 31), "2026-3"),
            (date(2025, 1, 1), "2025-4"),   # Jan-Mar closes the FY it names
            (date(2025, 3, 31), "2025-4"),
        ],
    )
    def test_labels_match_the_indian_fiscal_year(self, day, expected):
        assert str(current_quarter(day)) == expected

    def test_every_month_maps_somewhere(self):
        for month in range(1, 13):
            q = current_quarter(date(2025, month, 15))
            assert 1 <= q.quarter <= 4


class TestExpectedReportedPeriod:
    @pytest.mark.parametrize(
        "day, expected",
        [
            (date(2025, 5, 1), (3, 2025)),    # in FY-Q1 we await the March results
            (date(2025, 8, 1), (6, 2025)),
            (date(2025, 11, 1), (9, 2025)),
            (date(2025, 2, 1), (12, 2024)),   # FY-Q4 awaits the prior December
        ],
    )
    def test_awaits_the_previous_quarter_close(self, day, expected):
        assert expected_reported_period(day) == expected


class TestIsCurrent:
    def test_matching_period_is_current(self):
        assert is_current((3, 2025), date(2025, 5, 1))

    def test_stale_period_is_not(self):
        assert not is_current((12, 2024), date(2025, 5, 1))

    def test_missing_period_is_not(self):
        assert not is_current(None, date(2025, 5, 1))
