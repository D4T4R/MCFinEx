"""Merging two exchanges' closing prices into one update.

Kept separate from both sources and from the CLI because this is a policy, not a
transport: which exchange wins, and how a listing is matched to a stored company.
Pure, so the policy can be tested without touching the network or a database.

**Matched by ISIN, not by ticker.** A company can carry different symbols on the
two exchanges, so matching BSE rows on ``TckrSymb`` would silently miss the ones
that differ -- silently, because a price update only touches rows it already
knows, so a miss writes nothing and raises nothing.

**NSE wins.** Every ticker already stored is an NSE symbol, and NSE carries the
larger share of volume, so its close is the more meaningful number. BSE only
fills gaps: a company that did not trade on NSE that day but did on BSE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from .sources.bhavcopy import Listing


@dataclass
class Merged:
    """The price map to write, and where each figure came from."""

    prices: dict[str, float] = field(default_factory=dict)
    #: Tickers priced from each exchange, for reporting. A company appears in
    #: exactly one of these.
    by_exchange: dict[str, list[str]] = field(default_factory=dict)
    #: Listings that had a close but matched no stored company. Expected and
    #: large -- the exchanges list thousands of instruments we do not track --
    #: so counted rather than named.
    unmatched: int = 0

    def summary(self) -> str:
        parts = [f"{ex} {len(t):,}" for ex, t in sorted(self.by_exchange.items())]
        return f"{len(self.prices):,} priced ({', '.join(parts)})"


def merge(primary: Iterable[Listing], secondary: Iterable[Listing],
          isin_to_ticker: Mapping[str, str],
          known_tickers: set[str] | None = None) -> Merged:
    """One price per company, preferring ``primary``.

    ``primary`` listings are keyed on **their own ticker**, which is what the
    single-exchange version did and has to keep doing. 21 stored companies have
    no ISIN recorded at all, so they are absent from ``isin_to_ticker``; matching
    them through it would resolve to nothing and quietly stop pricing them. A
    ticker match is safe here because every stored ticker *is* a primary-exchange
    symbol.

    ``known_tickers`` is optional and only sharpens that: when a primary symbol is
    not a company we hold but its ISIN is, the ISIN wins. That covers a symbol
    rename, where the exchange has moved on and the database has not.

    ``secondary`` listings are matched by ISIN only. Their tickers belong to the
    other exchange's namespace, so a ticker match there would be a coincidence
    rather than an identification -- and a coincidence that writes one company's
    price onto another.
    """
    result = Merged()

    for listing in primary:
        if listing.close is None:
            continue
        ticker = listing.ticker
        if known_tickers is not None and ticker not in known_tickers:
            renamed = isin_to_ticker.get(listing.isin)
            if renamed is None:
                # Not a company we track. Expected in bulk: the exchange lists
                # thousands we have never scraped.
                result.unmatched += 1
                continue
            ticker = renamed
        if ticker in result.prices:
            continue
        result.prices[ticker] = listing.close
        result.by_exchange.setdefault(listing.exchange or "primary", []).append(ticker)

    for listing in secondary:
        if listing.close is None:
            continue
        ticker = isin_to_ticker.get(listing.isin)
        if ticker is None:
            result.unmatched += 1
            continue
        if ticker in result.prices:
            # Already priced by the primary exchange. This is the "do not run
            # twice for the same stock" case, and it is the common one: 2,395 of
            # 2,543 NSE listings also traded on BSE on 2026-09-07.
            continue
        result.prices[ticker] = listing.close
        result.by_exchange.setdefault(listing.exchange or "secondary", []).append(ticker)

    return result


@dataclass(frozen=True)
class Rename:
    """A company the exchange now calls something else."""

    isin: str
    old: str
    new: str
    #: True when the old row is the one holding the scraped history, so the
    #: rename has something to carry. A rename of an empty row is just tidying.
    carries_history: bool = False

    def __str__(self) -> str:
        return f"{self.old} -> {self.new} ({self.isin})"


def find_renames(listings: Iterable[Listing], stored_by_isin: Mapping[str, list[str]],
                 live_tickers: set[str]) -> tuple[list[Rename], dict[str, list[str]]]:
    """Symbols the exchange has moved on from, and the ones it has not.

    A rename is only safe to infer when the evidence is unambiguous: the feed
    lists this ISIN under some symbol, and the symbol the database holds it
    under is **not in the feed at all**. A stored symbol that is still trading
    is not an old name, whatever else shares its ISIN.

    Returns the renames to apply, and the ISINs left ambiguous -- more than one
    stored ticker still live, or none of them identifiable. Those are reported
    rather than guessed: picking wrong merges two companies' histories, which no
    later run can undo.
    """
    feed_by_isin: dict[str, str] = {}
    for listing in listings:
        if listing.isin:
            feed_by_isin.setdefault(listing.isin, listing.ticker)

    renames: list[Rename] = []
    ambiguous: dict[str, list[str]] = {}
    for isin, tickers in stored_by_isin.items():
        current = feed_by_isin.get(isin)
        if current is None:
            # Not trading under any symbol today. Could be suspended or
            # delisted; either way the feed offers no new name to move to.
            if len(tickers) > 1:
                ambiguous[isin] = sorted(tickers)
            continue
        stale = [t for t in tickers if t not in live_tickers and t != current]
        if len(stale) != len(tickers) - (1 if current in tickers else 0):
            # At least one stored symbol other than the current one is still
            # trading, so these are not two names for one company.
            ambiguous[isin] = sorted(tickers)
            continue
        for old in stale:
            renames.append(Rename(isin=isin, old=old, new=current))
    return renames, ambiguous


def new_listings(known_isins: set[str], candidates: Iterable[Listing]) -> list[Listing]:
    """Candidates whose ISIN is not already tracked, deduplicated.

    Used when seeding: a company that already exists under its NSE symbol must
    not be seeded again under its BSE one, which is what matching on ticker
    would do. Keyed on ISIN because that is the only identifier the two
    exchanges share.
    """
    seen: set[str] = set()
    out: list[Listing] = []
    for listing in candidates:
        if not listing.isin or listing.isin in known_isins or listing.isin in seen:
            continue
        seen.add(listing.isin)
        out.append(listing)
    return out
