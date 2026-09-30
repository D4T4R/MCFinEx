"""Closing prices from the BSE daily bhavcopy.

BSE publishes the same UDiFF format as NSE, so the row mapping is shared (see
:mod:`mcfinex.sources.bhavcopy`). Three things differ, and each one is a way to
get this wrong:

**It answers 200 for a day it has nothing for.** NSE returns 404 for a weekend;
BSE returns a 14 KB HTML page with a 200. Parsed as CSV that yields no rows, and
because a price update only touches companies it already knows, no rows writes
nothing and reports success. So the body is validated, not the status code.

**Only the bare ``.CSV`` works.** The sibling ``.csv.zip`` URL also answers 200,
with the same HTML page, on trading days too.

**``SctySrs`` means something else.** NSE has two equity series; BSE has a dozen
groups encoding board, liquidity and compliance. EQ and BE appear nowhere, so the
NSE filter would keep exactly zero BSE rows.

BSE is a *secondary* source here. NSE carries the overwhelming share of volume
and every ticker already stored is an NSE symbol, so NSE's close wins wherever
both have one and this only fills gaps. A caller should treat a failure here as
missing data, not as a failed run.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import requests

from .bhavcopy import Listing, looks_like_udiff, parse_udiff

log = logging.getLogger(__name__)

EXCHANGE = "BSE"

BHAVCOPY_URL = (
    "https://www.bseindia.com/download/BhavCopy/Equity/"
    "BhavCopy_BSE_CM_0_0_0_{yyyymmdd}_F_0000.CSV"
)
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

#: Groups that are a company's ordinary shares, whatever board they sit on.
#: Used for pricing, where the question is only "is this the equity of something
#: we might track", not "would we choose to track it".
#:
#: A, B      main board
#: T         trade-to-trade, settled gross (a surveillance measure, still equity)
#: X, XT     small and illiquid, XT trade-to-trade
#: M, MT, MS, TS   the SME platform
#: Z, ZP     listed but not complying with listing requirements
#: P         physical / odd-lot segment
#:
#: Deliberately excluded, because they are not companies with financials:
#: F (bonds and debentures -- note these carry INE ISINs too, so the ISIN prefix
#: does not separate them), G (government securities), E (ETFs), IF (InvITs and
#: REITs), R (rights entitlements, which are temporary instruments).
EQUITY_GROUPS = frozenset({"A", "B", "T", "X", "XT", "M", "MT", "MS", "TS", "Z", "ZP", "P"})

#: Groups worth *seeding the universe* from. The main board only: of 2,032
#: BSE-only listings measured on 2026-09-07, 1,474 were X/XT illiquids, 386 were
#: SME-platform names and 82 were flagged non-compliant -- median turnover across
#: the lot was Rs 1.5 lakh for the day, and 876 traded under Rs 1 lakh. Their
#: filings are thin and irregular, which is exactly the input a mechanical screen
#: reads worst. Widening this is a one-line change if that judgement changes.
MAIN_BOARD_GROUPS = frozenset({"A", "B", "T"})

#: Where the BSE scrip code lives. It matters well beyond pricing: screener.in
#: keys a company on this number, and for a company BSE lists and NSE does not it
#: is the *only* way to reach the page -- /company/NSE/ is a 404 while
#: /company/544937/ is National Stock Exchange of India Ltd. Without it a
#: BSE-only company can be seeded and priced but never scraped, so it never
#: reaches the screen and never appears in the app.
SCRIP_CODE_COLUMN = "FinInstrmId"

RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 2.0


class BseError(RuntimeError):
    pass


def fetch_bhavcopy(day: date, *, session: requests.Session | None = None,
                   timeout: float = 30.0, attempts: int = RETRY_ATTEMPTS) -> bytes | None:
    """Download one day's bhavcopy, or ``None`` if BSE has nothing for that day.

    "Nothing for that day" is detected from the body, because BSE reports it as a
    successful HTML page rather than a 404. A timeout or a 5xx is a different
    thing -- not an answer -- and is retried.
    """
    sess = session or requests.Session()
    url = BHAVCOPY_URL.format(yyyymmdd=day.strftime("%Y%m%d"))
    for attempt in range(1, attempts + 1):
        try:
            resp = sess.get(
                url,
                headers={"User-Agent": USER_AGENT, "Referer": "https://www.bseindia.com/"},
                timeout=timeout,
            )
            if resp.status_code == 404:
                return None
            if resp.status_code >= 500:
                raise requests.HTTPError(f"{resp.status_code} from {url}", response=resp)
            resp.raise_for_status()
            if not looks_like_udiff(resp.content):
                # A weekend, a holiday, or not published yet. Logged at debug
                # because the walk-back hits this several times on a Monday and
                # it is not news.
                log.debug("bse bhavcopy %s: not a bhavcopy (%d bytes), treating as absent",
                          day, len(resp.content))
                return None
            return resp.content
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and status < 500:
                raise
            if attempt == attempts:
                raise
            pause = RETRY_BACKOFF ** (attempt - 1)
            log.warning("bse bhavcopy %s attempt %d/%d failed (%s); retrying in %.0fs",
                        day, attempt, attempts, type(exc).__name__, pause)
            time.sleep(pause)
    return None  # unreachable; every path above returns or raises


def _fetch_or_skip(day: date, sess: requests.Session) -> bytes | None:
    """Fetch a day, treating a persistent network failure as a missing session."""
    try:
        return fetch_bhavcopy(day, session=sess)
    except requests.RequestException as exc:
        log.warning("bse bhavcopy %s unavailable after retries (%s); skipping that session",
                    day, exc)
        return None


def latest_bhavcopy(*, on: date | None = None, max_lookback: int = 10,
                    session: requests.Session | None = None) -> tuple[date, bytes]:
    """Walk back from ``on`` to the most recent published bhavcopy."""
    sess = session or requests.Session()
    start = on or date.today()
    for offset in range(max_lookback + 1):
        day = start - timedelta(days=offset)
        payload = _fetch_or_skip(day, sess)
        if payload is not None:
            return day, payload
    raise BseError(
        f"no bse bhavcopy found in the {max_lookback} days before {start.isoformat()}"
    )


def parse_bhavcopy(payload: bytes, *,
                   groups: frozenset[str] = EQUITY_GROUPS) -> list[Listing]:
    """Extract the equity rows from a bhavcopy CSV.

    Unzipped, unlike NSE: BSE serves the CSV directly.
    """
    if not looks_like_udiff(payload):
        raise BseError(
            "this is not a BSE bhavcopy -- BSE serves an HTML page with HTTP 200 "
            "for a day it has no file for"
        )
    return parse_udiff(payload.decode("utf-8-sig"), series=groups, exchange=EXCHANGE,
                       security_id_column=SCRIP_CODE_COLUMN)


def main_board(payload: bytes) -> list[Listing]:
    """Only the main-board rows, for deciding what is worth tracking."""
    return parse_bhavcopy(payload, groups=MAIN_BOARD_GROUPS)


def universe(*, days: int = 7, on: date | None = None,
             session: requests.Session | None = None,
             groups: frozenset[str] = MAIN_BOARD_GROUPS) -> tuple[list[Listing], list[date]]:
    """The traded main board, unioned over the last ``days`` sessions.

    Same reason as NSE: one file lists only what traded, so a single day
    undercounts by however many small caps sat still. The newest price wins.
    """
    sess = session or requests.Session()
    start = on or date.today()
    found: dict[str, Listing] = {}
    sessions: list[date] = []
    for offset in range(days * 2):
        if len(sessions) >= days:
            break
        day = start - timedelta(days=offset)
        payload = _fetch_or_skip(day, sess)
        if payload is None:
            continue
        sessions.append(day)
        for listing in parse_bhavcopy(payload, groups=groups):
            found.setdefault(listing.isin, listing)
    if not sessions:
        raise BseError(f"no bse bhavcopy found in the {days * 2} days before {start}")
    return sorted(found.values(), key=lambda l: l.ticker), sessions
