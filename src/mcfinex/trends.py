"""Eight-quarter trend analysis and a mechanical next-quarter projection.

Quarterly results are seasonal: a sweet shop's December is not comparable with
its August. So growth here is measured **year over year** -- each quarter
against the same quarter twelve months earlier -- and smoothed through trailing
twelve month totals. The workbook's quarter-on-quarter comparison mistook
seasonality for momentum, which is why its quarterly EPS signal was so noisy.

The projection is a seasonal naive forecast with drift: next quarter is assumed
to look like the same quarter last year, grown at the recent year-over-year
rate. It is arithmetic on published figures, not a prediction, and it carries a
confidence label derived from how stable that growth has been.

Pure: no database, no UI, no I/O.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date
from enum import Enum

#: Quarterly lines worth trending, with their bank/NBFC aliases. Lives here
#: rather than in the API so the UI does not have to import a web framework.
TREND_LINES = (
    ("Sales", ("Sales", "Revenue")),
    ("Operating Profit", ("Operating Profit", "Financing Profit")),
    ("EPS in Rs", ("EPS in Rs",)),
)

#: Quarters needed before a year-over-year comparison is possible at all.
MIN_PERIODS = 5
#: The window the analysis is built for.
WINDOW = 8


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


@dataclass
class Trend:
    """One line item's recent history, oldest first."""

    label: str
    periods: list[date]
    values: list[float]
    #: Year-over-year growth as a percentage, aligned to `periods[4:]`.
    yoy_growth: list[float | None]
    ttm: float | None = None
    ttm_prior: float | None = None
    ttm_growth_pct: float | None = None
    forecast: float | None = None
    forecast_period: str | None = None
    confidence: Confidence = Confidence.NONE
    note: str = ""

    @property
    def latest(self) -> float | None:
        return self.values[-1] if self.values else None

    @property
    def improving(self) -> bool | None:
        if self.ttm_growth_pct is None:
            return None
        return self.ttm_growth_pct > 0


def analyse(label: str, periods: list[date], values: list[float]) -> Trend:
    """Build the trend for one line item. Inputs must be oldest first."""
    periods, values = list(periods[-WINDOW:]), list(values[-WINDOW:])
    trend = Trend(label=label, periods=periods, values=values, yoy_growth=[])

    if len(values) < MIN_PERIODS:
        trend.note = f"needs {MIN_PERIODS} quarters for a year-on-year comparison"
        return trend

    # Each quarter against the same quarter a year earlier.
    for index in range(4, len(values)):
        prior, current = values[index - 4], values[index]
        trend.yoy_growth.append(_growth_pct(prior, current))

    if len(values) >= 8:
        trend.ttm = sum(values[-4:])
        trend.ttm_prior = sum(values[-8:-4])
        trend.ttm_growth_pct = _growth_pct(trend.ttm_prior, trend.ttm)

    _project(trend)
    return trend


def _project(trend: Trend) -> None:
    """Seasonal naive forecast with drift, plus a confidence label."""
    usable = [g for g in trend.yoy_growth if g is not None]
    if not usable or len(trend.values) < 4:
        trend.note = trend.note or "not enough comparable quarters to project"
        return

    # Base is the same quarter last year, grown at the average recent YoY rate.
    same_quarter_last_year = trend.values[-4]
    rate = statistics.mean(usable[-4:]) / 100.0
    trend.forecast = same_quarter_last_year * (1 + rate)
    trend.forecast_period = _next_quarter_label(trend.periods[-1])
    trend.confidence = _confidence(usable)


def _confidence(growths: list[float]) -> Confidence:
    """How consistent the year-over-year growth has been.

    A projection off a rate that swings between +80% and -40% deserves to be
    labelled differently from one off a steady 12%.
    """
    if len(growths) < 2:
        return Confidence.LOW
    spread = statistics.pstdev(growths)
    if spread < 15:
        return Confidence.HIGH
    if spread < 40:
        return Confidence.MEDIUM
    return Confidence.LOW


def _growth_pct(prior: float | None, current: float | None) -> float | None:
    if prior is None or current is None or prior == 0:
        return None
    # A negative base inverts the ratio; the sign correction keeps "improved"
    # positive even when the company was loss-making.
    return (current - prior) / abs(prior) * 100


def _next_quarter_label(last: date) -> str:
    """The quarter after ``last``, as e.g. ``"Sep 2026"``."""
    month = last.month + 3
    year = last.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return f"{date(year, month, 1):%b %Y}"
