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


def parse_udiff(text: str, *, series: frozenset[str], exchange: str = "") -> list[Listing]:
    """Map UDiFF rows to listings, keeping only ``series`` and real equity.

    Rows with no ticker are dropped rather than carried as blanks: the ticker is
    the identity, and a listing without one cannot be matched to anything.
    """
    listings: list[Listing] = []
    for row in csv.DictReader(io.StringIO(text)):
        if (row.get("SctySrs") or "").strip().upper() not in series:
            continue
        ticker = (row.get("TckrSymb") or "").strip().upper()
        isin = (row.get("ISIN") or "").strip()
        if not ticker or isin.startswith(FUND_ISIN_PREFIX):
            continue
        listings.append(
            Listing(
                ticker=ticker,
                isin=isin,
                name=(row.get("FinInstrmNm") or "").strip(),
                close=to_float(row.get("ClsPric")),
                exchange=exchange,
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
