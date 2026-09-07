"""Publish the screen as a static site.

The phone app reads JSON files, not a server. The data changes once a night and
the audience is a handful of people, so a hosted API would be a process to keep
alive, a cold start to wait through and a credential to rotate, in exchange for
answering the same question with the same answer all day.

Two shapes are written. ``index.json`` is everything the browse screens need --
counts, sector heat, and every ranked pick -- at about 140 KB over the wire, so
the app fetches it once and works from memory. Per-company detail is a file each,
fetched on tap, because bundling signals and trends for the whole shortlist would
turn a 140 KB download into 4 MB to show one company.

The payload builders are shared with :mod:`mcfinex.api` rather than duplicated.
Two serialisers for the same objects drift, and the drift shows up as a field the
app reads and the API stopped sending.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import picks as picks_module
from .disclaimer import FULL as FULL_DISCLAIMER, SHORT as SHORT_DISCLAIMER
from .trends import TREND_LINES, analyse

#: Bumped when a field is removed or changes meaning. The app is sideloaded, so
#: there is no way to force anyone to update: an old build has to be able to
#: recognise a payload it cannot read and say so, rather than crash or quietly
#: display nonsense.
SCHEMA = 1

#: Every quarterly line any trend needs, flattened for one bulk fetch.
TREND_LABELS: tuple[str, ...] = tuple(
    dict.fromkeys(alias for _, aliases in TREND_LINES for alias in aliases)
)

_UNSAFE = re.compile(r"[^A-Z0-9-]")


def file_id(ticker: str) -> str:
    """A ticker as a URL-safe filename stem.

    NSE tickers are alphanumeric apart from ``-`` and ``&``. The hyphen is safe
    in a path; the ampersand is legal too, strictly, but it is the character
    most likely to be mangled by something between here and the phone, so it is
    mapped out. ``_`` never occurs in a ticker, which makes the mapping
    injective rather than merely lucky -- but :func:`write_site` asserts that
    anyway, because "provably fine today" and "fine after the next listing" are
    different claims.
    """
    return _UNSAFE.sub("_", ticker.upper())


def money(value: float | None) -> float | None:
    """Two decimals, because these are rupees and percentages.

    A target price computed in floating point serialises as
    ``78.49408469961412``: eighteen characters of which five carry meaning, and
    the rest is the arithmetic showing through. Nothing displays more than two
    decimals, so the extra precision is only ever transfer.
    """
    return None if value is None else round(value, 2)


def moneys(values: Iterable[float | None]) -> list[float | None]:
    """:func:`money` over a series. Kept separate rather than making ``money``
    accept either, because a helper that quietly handles both is a helper that
    quietly handles a mistake."""
    return [money(v) for v in values]


def pick_payload(pick: picks_module.Pick) -> dict[str, Any]:
    """One ranked candidate, as both the app and the API send it."""
    return {
        "ticker": pick.ticker,
        "name": pick.name,
        "sector": pick.sector,
        "price": money(pick.price),
        "target": money(pick.target),
        "entry_3by4": money(pick.entry_3by4),
        "entry_2by3": money(pick.entry_2by3),
        "upside_pct": money(pick.upside_pct),
        "discount_to_entry_pct": money(pick.discount_to_entry_pct),
        "actionable": pick.is_actionable,
        "buy_signals": pick.buy_signals,
        "sell_signals": pick.sell_signals,
        "scored": pick.scored,
        "models_agreeing": pick.models_agreeing,
        "quality_buys": pick.quality_buys,
        "quality_sells": pick.quality_sells,
        "tier": pick.tier.value,
        "flags": pick.flags,
    }


def signal_payload(signal) -> dict[str, Any]:
    return {
        "key": signal.key,
        "label": signal.label,
        "verdict": signal.verdict.value,
        "value": money(signal.value) if isinstance(signal.value, float) else signal.value,
        "rule": signal.rule,
        "available": signal.available,
    }


def trend_payload(label: str, history: Sequence[tuple[str, float]]) -> dict[str, Any] | None:
    """An eight-quarter trend, or ``None`` when there is nothing to analyse."""
    if not history:
        return None
    periods = [date.fromisoformat(period) for period, _ in history]
    trend = analyse(label, periods, [value for _, value in history])
    return {
        "label": trend.label,
        "periods": [f"{p:%b %Y}" for p in trend.periods],
        "values": moneys(trend.values),
        # A series aligned to ``periods[4:]``, not a scalar: year-over-year
        # needs four quarters of runway before it can be computed at all.
        "yoy_growth_pct": moneys(trend.yoy_growth),
        "ttm": money(trend.ttm),
        "ttm_prior": money(trend.ttm_prior),
        "ttm_growth_pct": money(trend.ttm_growth_pct),
        "forecast": money(trend.forecast),
        "forecast_period": trend.forecast_period,
        "confidence": trend.confidence.value,
        "note": trend.note,
    }


def trends_for(histories: Mapping[str, list[tuple[str, float]]]) -> list[dict[str, Any]]:
    """Every trend one company has data for.

    Each line has aliases because screener names the same thing differently for
    financial companies -- "Financing Profit" rather than "Operating Profit" --
    so the first alias with history wins and the rest are not consulted.
    """
    found = []
    for label, aliases in TREND_LINES:
        for alias in aliases:
            payload = trend_payload(label, histories.get(alias, []))
            if payload is not None:
                found.append(payload)
                break
    return found


def company_payload(row, histories: Mapping[str, list[tuple[str, float]]]) -> dict[str, Any]:
    """Everything a detail screen shows for one company."""
    return {
        "schema": SCHEMA,
        **pick_payload(picks_module.to_pick(row)),
        "targets": {
            "ev_ebitda": money(row.target_ev_ebitda),
            "pe_yearly": money(row.target_pe_yearly),
            "pe_quarterly": money(row.target_pe_quarterly),
        },
        "signals": [signal_payload(s) for s in row.screening.signals],
        "trends": trends_for(histories),
        "disclaimer": SHORT_DISCLAIMER,
    }


def index_payload(rows: Sequence, *, priced: str | None, scraped: str | None,
                  generated: str | None = None) -> dict[str, Any]:
    """The browse payload: counts, sector heat, and every ranked pick."""
    ranked = picks_module.rank(rows)
    tiers: dict[str, int] = {}
    for row in rows:
        tier = picks_module.classify(row).value
        tiers[tier] = tiers.get(tier, 0) + 1

    return {
        "schema": SCHEMA,
        "generated": generated or datetime.now(timezone.utc).replace(
            microsecond=0).isoformat().replace("+00:00", "Z"),
        "price_date": priced,
        "last_scraped": scraped,
        # Everything screened, including the names that reached no tier, so the
        # app can say "111 of 2,544" rather than "111 of 1,569" -- the latter
        # reads as a far less selective screen than it is.
        "universe": len(rows),
        "tiers": tiers,
        "sectors": [
            {
                "sector": h.sector, "picks": h.picks, "total": h.total,
                "share_pct": money(h.share_pct),
                "median_upside_pct": money(h.median_upside_pct),
            }
            for h in picks_module.sector_heat(rows, limit=20)
        ],
        # `id` travels with the pick so the client never has to reimplement the
        # filename rule. It differs from the ticker for fifteen companies, which
        # is exactly the kind of thing that works everywhere except M&M.
        "picks": [{"id": file_id(p.ticker), **pick_payload(p)} for p in ranked],
        "disclaimer": SHORT_DISCLAIMER,
        "disclaimer_full": FULL_DISCLAIMER,
    }


@dataclass(frozen=True)
class Written:
    directory: Path
    companies: int
    total_bytes: int

    @property
    def summary(self) -> str:
        return (f"{self.companies:,} companies, "
                f"{self.total_bytes / 1024 / 1024:.1f} MB into {self.directory}")


def _dump(path: Path, payload: dict[str, Any]) -> int:
    """Write compact JSON and report the size.

    Compact rather than indented: this is read by a program over a mobile
    connection, and the whitespace is a third of the transfer.
    """
    text = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def write_site(store, out: Path, rows: Iterable | None = None) -> Written:
    """Build the whole static site under ``out``.

    Detail files are written only for companies that reached a tier. The other
    975 are not shown anywhere in the app, and writing them would triple the
    publish for pages nobody can navigate to.
    """
    from .report import screen_all

    rows = list(rows) if rows is not None else screen_all(store)
    priced, scraped = store.data_freshness()

    out.mkdir(parents=True, exist_ok=True)
    company_dir = out / "company"
    company_dir.mkdir(exist_ok=True)

    payload = index_payload(rows, priced=priced, scraped=scraped)
    total = _dump(out / "index.json", payload)

    wanted = {p["ticker"]: p["id"] for p in payload["picks"]}
    _check_ids(wanted)

    histories = store.all_quarterly_history(TREND_LABELS)
    written = 0
    for row in rows:
        ticker = row.screening.ticker
        stem = wanted.get(ticker)
        if stem is None:
            continue
        total += _dump(company_dir / f"{stem}.json",
                       company_payload(row, histories.get(ticker, {})))
        written += 1

    return Written(out, written, total)


def _check_ids(ids: Mapping[str, str]) -> None:
    """Refuse to publish if two tickers want the same filename.

    A collision would silently serve one company's detail under another's name,
    and the only symptom would be a reader looking at the wrong numbers.
    """
    seen: dict[str, str] = {}
    for ticker, stem in ids.items():
        if stem in seen:
            raise ValueError(
                f"{ticker} and {seen[stem]} both publish as {stem}.json; "
                "the filename rule in file_id() needs widening"
            )
        seen[stem] = ticker
