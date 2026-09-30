"""The parts of a bhavcopy that NSE and BSE agree on.

Both exchanges publish end-of-day equities in SEBI's UDiFF format, with the same
column names -- ``ISIN``, ``TckrSymb``, ``SctySrs``, ``FinInstrmNm``, ``ClsPric``
-- so the row mapping is written once here rather than twice.

What they do *not* agree on is everything around it: NSE serves a ZIP and answers
404 for a day it has nothing for, while BSE serves a bare CSV and answers 200
with an HTML page. Those differences live in :mod:`mcfinex.sources.nse` and
:mod:`mcfinex.sources.bse`, because pretending they are the same is how a
non-trading day turns into an empty price update that looks like a successful one.

``SctySrs`` is the same column but a different vocabulary: NSE says EQ and BE,
BSE says A, B, T, X, M and nine others. So the filter is a parameter, not a
constant.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

# ETFs and mutual fund units trade alongside equity but are not companies and
# have no financial statements to screen. Indian ISINs encode this: INE is an
# equity share, INF a fund unit. The Java original filtered on exactly this and
# the check was lost in the rewrite.
FUND_ISIN_PREFIX = "INF"

#: An Indian ISIN is IN + issuer type + a four-character company code + a
#: two-digit *security* type at [7:9] + serial + check digit. ``01`` is ordinary
#: equity, and it is what a company page on screener describes.
#:
#: Everything else in that field is an instrument with no financials of its own,
#: so seeding one guarantees a row that can never be scraped -- and an
#: unscrapeable row sits at the head of the backlog being retried every night.
#: Measured on 2026-09-30, requiring ``01`` excluded exactly two rows across both
#: exchanges: CENTEXT-RE, a rights entitlement carried in NSE's BE series, and
#: QDLCCPS, Quint Digital's convertible preference shares on BSE. 4,322 of 4,323
#: BSE equity rows and 2,727 of 2,728 NSE ones are ``01``.
#:
#: Kept as a set so widening it is one edit. If a real company is ever excluded
#: the symptom is the one this project keeps running into -- a company silently
#: absent -- so the count of skipped rows is reported by the caller.
EQUITY_ISIN_TYPES = frozenset({"01"})


def is_ordinary_equity(isin: str) -> bool:
    """Whether an ISIN denotes a company's ordinary shares.

    A malformed or short ISIN fails rather than passes: the point is to seed only
    what can be scraped, and an identifier we cannot read is not evidence that we
    can.
    """
    return len(isin) >= 9 and isin[7:9] in EQUITY_ISIN_TYPES


@dataclass(frozen=True)
class Listing:
    """One traded instrument on one day.

    ``exchange`` is carried so a merged price map can say where each figure came
    from. Defaulted, because it was added when BSE was: the field is provenance,
    not identity, and nothing should have to supply it to construct a listing.
    """

    ticker: str
    isin: str
    name: str
    close: float | None
    exchange: str = ""
    #: The exchange's own instrument id, when it is one anything else can use.
    #: Only BSE fills this: its ``FinInstrmId`` is the scrip code, which is what
    #: screener.in keys a company on and the only way to reach one that is not
    #: listed on NSE. NSE's column holds an internal token meaningless elsewhere,
    #: so it is deliberately left empty rather than stored and later trusted.
    security_id: str | None = None


def parse_udiff(text: str, *, series: frozenset[str], exchange: str = "",
                security_id_column: str | None = None) -> list[Listing]:
    """Map UDiFF rows to listings, keeping only ``series`` and real equity.

    Rows with no ticker are dropped rather than carried as blanks: the ticker is
    the identity, and a listing without one cannot be matched to anything.

    ``security_id_column`` is named by the caller rather than assumed, because
    both exchanges have a ``FinInstrmId`` and only one of them means anything
    outside its own feed.
    """
    listings: list[Listing] = []
    for row in csv.DictReader(io.StringIO(text)):
        if (row.get("SctySrs") or "").strip().upper() not in series:
            continue
        ticker = (row.get("TckrSymb") or "").strip().upper()
        isin = (row.get("ISIN") or "").strip()
        if not ticker or isin.startswith(FUND_ISIN_PREFIX):
            continue
        if not is_ordinary_equity(isin):
            # A rights entitlement or a preference share. It trades in an equity
            # series, has no company page, and if seeded would be retried by
            # every scrape of the backlog for as long as the row exists.
            continue
        security_id = None
        if security_id_column:
            security_id = (row.get(security_id_column) or "").strip() or None
        listings.append(
            Listing(
                ticker=ticker,
                isin=isin,
                name=(row.get("FinInstrmNm") or "").strip(),
                close=to_float(row.get("ClsPric")),
                exchange=exchange,
                security_id=security_id,
            )
        )
    return listings


def looks_like_udiff(payload: bytes) -> bool:
    """Whether a response body is actually a bhavcopy.

    BSE answers a request for a day it has no file for with HTTP 200 and a 14 KB
    HTML page. Handed to the parser that yields zero rows, and because a price
    update only touches companies it already knows, zero rows writes nothing and
    reports success -- a silent no-op every weekend. So the body is checked
    rather than the status code.

    Deliberately looking for the header rather than against ``<html``: an
    unexpected body of any kind should fail this, not just the one shape of
    error page BSE happens to serve today.
    """
    head = payload[:400].lstrip()
    return b"TradDt" in head and b"ISIN" in head


def to_float(text: str | None) -> float | None:
    if not text or not text.strip():
        return None
    try:
        return float(text.strip())
    except ValueError:
        return None
