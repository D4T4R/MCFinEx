"""Indian fiscal-quarter helpers.

The Indian financial year runs April to March. FY2026 Q1 is Apr-Jun 2025, so a
quarter label is written ``"2026-1"`` -- the FY it belongs to, then the quarter
number. This matches the ``LAST_UPDATED_QUARTER`` strings the original Java
build wrote into ``STOCK_MASTER``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

#: India Standard Time.
#:
#: A fixed offset rather than ``ZoneInfo("Asia/Kolkata")``: India has observed
#: no daylight saving since 1945, so +05:30 is exact for every date this deals
#: with, and a fixed offset needs no tzdata present on the host.
IST = timezone(timedelta(hours=5, minutes=30))


def in_ist(moment: datetime) -> datetime:
    """``moment`` as Indian market time. A naive input is read as UTC."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(IST)


def today() -> date:
    """The current date in Indian market time.

    The jobs run on UTC hosts and land around 01:10 IST, which is still the
    previous day in UTC. Anything dated for a reader here has to be dated by
    the market's day, or a run finishing just after midnight in Mumbai is filed
    under yesterday and the date never matches the reader's calendar.
    """
    return in_ist(datetime.now(timezone.utc)).date()


# Calendar month a results-period ends in, per fiscal quarter. During FY-Q1
# (Apr-Jun) the newest published results are for the period ending March.
_REPORTED_PERIOD_END_MONTH = {1: 3, 2: 6, 3: 9, 4: 12}


@dataclass(frozen=True)
class Quarter:
    """A fiscal quarter, e.g. FY2026 Q1."""

    fiscal_year: int
    quarter: int

    def __str__(self) -> str:
        return f"{self.fiscal_year}-{self.quarter}"


def current_quarter(today: date | None = None) -> Quarter:
    """The fiscal quarter that ``today`` falls inside."""
    today = today or date.today()
    month = today.month
    if month <= 3:
        return Quarter(today.year, 4)
    if month <= 6:
        return Quarter(today.year + 1, 1)
    if month <= 9:
        return Quarter(today.year + 1, 2)
    return Quarter(today.year + 1, 3)


def expected_reported_period(today: date | None = None) -> tuple[int, int]:
    """The ``(month, year)`` of the newest results that should be published.

    Companies report a quarter after it closes, so inside FY-Q1 (Apr-Jun) the
    freshest numbers cover the period ending March of the same calendar year.
    Returns a calendar ``(month, year)`` pair to compare against a scraped
    column heading such as ``"Mar 2025"``.
    """
    today = today or date.today()
    q = current_quarter(today)
    month = _REPORTED_PERIOD_END_MONTH[q.quarter]
    # FY-Q4 (Jan-Mar) waits on the December quarter of the *previous* calendar
    # year; every other fiscal quarter reports within the current one.
    year = today.year - 1 if q.quarter == 4 else today.year
    return month, year


def is_current(period: tuple[int, int] | None, today: date | None = None) -> bool:
    """Whether a scraped ``(month, year)`` period is the newest expected one."""
    if period is None:
        return False
    return period == expected_reported_period(today)
