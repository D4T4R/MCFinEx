"""Seed the company universe from the NSE daily bhavcopy.

The bhavcopy is the exchange's end-of-day dump of every traded instrument. It
supplies the ticker list, ISINs and closing prices that screener does not
publish, so it is what decides *which* companies to scrape.

NSE retired the ``archives.nseindia.com/content/historical/EQUITIES/<year>/<MON>/``
layout the Java build used -- that URL now returns 404 for every date. This
targets the current UDiFF feed instead.
"""

from __future__ import annotations

import io
import logging
import time
import zipfile
from datetime import date, timedelta

import requests

from .bhavcopy import FUND_ISIN_PREFIX, Listing, parse_udiff, to_float

log = logging.getLogger(__name__)

# Re-exported: the UDiFF row shape and the fund-unit rule are shared with BSE,
# but callers and tests have imported them from here since before there was a
# second exchange.
__all__ = [
    "BHAVCOPY_URL", "EQUITY_SERIES", "FUND_ISIN_PREFIX", "Listing", "NseError",
    "fetch_bhavcopy", "latest_bhavcopy", "parse_bhavcopy", "universe",
]

EXCHANGE = "NSE"

BHAVCOPY_URL = (
    "https://nsearchives.nseindia.com/content/cm/"
    "BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
)
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Only ordinary equity. GB is sovereign gold bonds, and the rest are debt and
# other instruments that have no company page on screener. BSE uses a completely
# different vocabulary in the same column -- see mcfinex.sources.bse.
EQUITY_SERIES = frozenset({"EQ", "BE"})


class NseError(RuntimeError):
    pass


#: Transient failures worth another go: nsearchives is intermittently slow, and
#: a single timeout used to abort the whole walk-back.
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 2.0


def fetch_bhavcopy(day: date, *, session: requests.Session | None = None,
                   timeout: float = 30.0, attempts: int = RETRY_ATTEMPTS) -> bytes | None:
    """Download one day's bhavcopy, or ``None`` if NSE has nothing for that day.

    A 404 is an answer -- weekend, holiday, or not published yet -- and is
    reported as ``None``. A timeout or a 5xx is not an answer, so it is retried
    before giving up: nsearchives regularly takes longer than 30s under load,
    and a single slow response should not decide the outcome of the run.
    """
    sess = session or requests.Session()
    url = BHAVCOPY_URL.format(yyyymmdd=day.strftime("%Y%m%d"))
    for attempt in range(1, attempts + 1):
        try:
            resp = sess.get(
                url,
                headers={"User-Agent": USER_AGENT, "Referer": "https://www.nseindia.com/"},
                timeout=timeout,
            )
            if resp.status_code == 404:
                return None
            if resp.status_code >= 500:
                raise requests.HTTPError(f"{resp.status_code} from {url}", response=resp)
            resp.raise_for_status()
            return resp.content
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            # 4xx other than 404 is a real answer -- a changed URL, say -- and
            # retrying it just wastes the run.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and status < 500:
                raise
            if attempt == attempts:
                raise
            pause = RETRY_BACKOFF ** (attempt - 1)
            log.warning("bhavcopy %s attempt %d/%d failed (%s); retrying in %.0fs",
                        day, attempt, attempts, type(exc).__name__, pause)
            time.sleep(pause)
    return None  # unreachable; every path above returns or raises


def _fetch_or_skip(day: date, sess: requests.Session) -> bytes | None:
    """Fetch a day, treating a persistent network failure as a missing session.

    The walk-back exists precisely so one unusable day does not sink the run,
    but it only ever handled 404s -- a timeout raised straight out of the loop.
    On a Saturday that meant the job could die requesting a file it did not
    need, before reaching the Friday one it did.

    Deliberately not silent: the day is logged and skipped, and the callers
    still raise if *no* session could be read at all.
    """
    try:
        return fetch_bhavcopy(day, session=sess)
    except requests.RequestException as exc:
        log.warning("bhavcopy %s unavailable after retries (%s); skipping that session",
                    day, exc)
        return None


def latest_bhavcopy(*, on: date | None = None, max_lookback: int = 10,
                    session: requests.Session | None = None) -> tuple[date, bytes]:
    """Walk back from ``on`` to the most recent published bhavcopy.

    ``max_lookback`` bounds the search. The Java equivalent looped
    ``while (!bGotFile)`` with no limit, so a network outage or a renamed URL
    spun forever instead of failing.
    """
    sess = session or requests.Session()
    start = on or date.today()
    for offset in range(max_lookback + 1):
        day = start - timedelta(days=offset)
        payload = _fetch_or_skip(day, sess)
        if payload is not None:
            return day, payload
    raise NseError(
        f"no bhavcopy found in the {max_lookback} days before {start.isoformat()}"
    )


def universe(*, days: int = 7, on: date | None = None,
             session: requests.Session | None = None) -> tuple[list[Listing], list[date]]:
    """The traded universe, unioned over the last ``days`` trading sessions.

    A bhavcopy only lists instruments that actually traded that day, so any
    single file undercounts: 2026-08-14 held 2,713 equity listings while a week
    unioned held 2,867. The gap is illiquid small caps that go days without a
    trade, not new listings. The newest price seen for a ticker wins.

    Returns the listings and the sessions actually found.
    """
    sess = session or requests.Session()
    start = on or date.today()
    found: dict[str, Listing] = {}
    sessions: list[date] = []
    # Walk backwards so newer sessions are seen first; older ones only fill gaps.
    for offset in range(days * 2):
        if len(sessions) >= days:
            break
        day = start - timedelta(days=offset)
        payload = _fetch_or_skip(day, sess)
        if payload is None:
            continue
        sessions.append(day)
        for listing in parse_bhavcopy(payload):
            found.setdefault(listing.ticker, listing)
    if not sessions:
        raise NseError(f"no bhavcopy found in the {days * 2} days before {start}")
    return sorted(found.values(), key=lambda l: l.ticker), sessions


def parse_bhavcopy(payload: bytes) -> list[Listing]:
    """Extract the equity rows from a bhavcopy ZIP."""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise NseError("bhavcopy archive contains no CSV")
        text = archive.read(names[0]).decode("utf-8-sig")
    return parse_udiff(text, series=EQUITY_SERIES, exchange=EXCHANGE)


#: Kept as a private alias: tests and older callers reached for it directly.
_float = to_float
