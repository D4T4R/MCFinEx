"""Seed the company universe from the NSE daily bhavcopy.

The bhavcopy is the exchange's end-of-day dump of every traded instrument. It
supplies the ticker list, ISINs and closing prices that screener does not
publish, so it is what decides *which* companies to scrape.

NSE retired the ``archives.nseindia.com/content/historical/EQUITIES/<year>/<MON>/``
layout the Java build used -- that URL now returns 404 for every date. This
targets the current UDiFF feed instead.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta

import requests

BHAVCOPY_URL = (
    "https://nsearchives.nseindia.com/content/cm/"
    "BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
)
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Only ordinary equity. GB is sovereign gold bonds, and the rest are debt and
# other instruments that have no company page on screener.
EQUITY_SERIES = frozenset({"EQ", "BE"})


class NseError(RuntimeError):
    pass


@dataclass(frozen=True)
class Listing:
    ticker: str
    isin: str
    name: str
    close: float | None


def fetch_bhavcopy(day: date, *, session: requests.Session | None = None,
                   timeout: float = 30.0) -> bytes | None:
    """Download one day's bhavcopy, or ``None`` if NSE has nothing for that day."""
    sess = session or requests.Session()
    url = BHAVCOPY_URL.format(yyyymmdd=day.strftime("%Y%m%d"))
    resp = sess.get(
        url,
        headers={"User-Agent": USER_AGENT, "Referer": "https://www.nseindia.com/"},
        timeout=timeout,
    )
    if resp.status_code == 404:
        return None  # weekend, holiday, or not published yet
    resp.raise_for_status()
    return resp.content


def latest_bhavcopy(*, on: date | None = None, max_lookback: int = 10,
                    session: requests.Session | None = None) -> tuple[date, bytes]:
    """Walk back from ``on`` to the most recent published bhavcopy.

    ``max_lookback`` bounds the search. The Java equivalent looped
    ``while (!bGotFile)`` with no limit, so a network outage or a renamed URL
    spun forever instead of failing.
    """
    start = on or date.today()
    for offset in range(max_lookback + 1):
        day = start - timedelta(days=offset)
        payload = fetch_bhavcopy(day, session=session)
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
        payload = fetch_bhavcopy(day, session=sess)
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

    listings: list[Listing] = []
    for row in csv.DictReader(io.StringIO(text)):
        if (row.get("SctySrs") or "").strip().upper() not in EQUITY_SERIES:
            continue
        ticker = (row.get("TckrSymb") or "").strip().upper()
        if not ticker:
            continue
        listings.append(
            Listing(
                ticker=ticker,
                isin=(row.get("ISIN") or "").strip(),
                name=(row.get("FinInstrmNm") or "").strip(),
                close=_float(row.get("ClsPric")),
            )
        )
    return listings


def _float(text: str | None) -> float | None:
    if not text or not text.strip():
        return None
    try:
        return float(text.strip())
    except ValueError:
        return None
