"""On-demand detail from screener's schedules API.

The company page collapses detail into ``Other Assets``, ``Other Liabilities``
and ``Borrowings``. Expanding them costs three extra requests, which is why it
is not part of the bulk scrape -- it would roughly double a 40-minute run for
data most screens never look at. Fetched per company, when asked.

What it unlocks, none of which is on the page:

* **Cash**, so enterprise value can finally net off cash rather than being
  ``market cap + debt``.
* **Current assets and liabilities**, so the current ratio stops being
  permanently UNKNOWN.
* **Long vs short term borrowings**, so the long-term figure is the real one
  rather than total debt.

Because those feed the EV/EBITDA model, enriching a company re-runs its
valuation and re-scores it.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

from .db.store import Store
from .pipeline import revalue
from .sources import screener

#: statement name under which schedule line items are stored
SCHEDULE = "schedule"

# Parent rows worth expanding, with the alias screener uses for banks.
SCHEDULE_PARENTS = ("Other Assets", "Other Liabilities", "Borrowings", "Borrowing")

# Which schedule line items roll up into each derived figure. Matched
# case-insensitively on a prefix, since screener's wording drifts
# ("Cash Equivalents", "Loans n Advances").
CURRENT_ASSET_ITEMS = ("inventories", "trade receivables", "cash equivalents", "loans n advances")
CURRENT_LIABILITY_ITEMS = ("trade payables", "advance from customers", "short term borrowings")
CASH_ITEMS = ("cash equivalents",)
LONG_TERM_ITEMS = ("long term borrowings",)


@dataclass
class Enrichment:
    ticker: str
    items: int = 0
    cash: float | None = None
    current_assets: float | None = None
    current_liabilities: float | None = None
    long_term_borrowings: float | None = None
    revalued: bool = False

    @property
    def found_anything(self) -> bool:
        return self.items > 0


def enrich(store: Store, ticker: str, *, session: requests.Session | None = None,
           throttle: screener.Throttle | None = None) -> Enrichment:
    """Pull a company's schedules, store them, and re-run its valuation."""
    company = store.company(ticker)
    if company is None:
        return Enrichment(ticker=ticker)

    sess = session or requests.Session()
    company_id = company["company_id"]
    if company_id is None:
        # Companies scraped before the id was captured need one page fetch to
        # find it. Stored so the next enrichment costs only the schedules.
        page = screener.fetch(ticker, session=sess, throttle=throttle)
        company_id = screener.parse(page, ticker).company_id
        if company_id is None:
            return Enrichment(ticker=ticker)
        store.upsert_company(ticker, {"company_id": company_id})
    rows: list[tuple[str, str, str, float | None]] = []
    seen_parents: set[str] = set()

    for parent in SCHEDULE_PARENTS:
        schedule = screener.fetch_schedule(
            company_id, parent, session=sess, throttle=throttle
        )
        if not schedule:
            continue
        seen_parents.add(parent)
        for label, periods in schedule.items():
            if not isinstance(periods, dict):
                continue
            for period, raw in periods.items():
                # Nested expandables carry a JS call instead of a number.
                value = screener.to_number(raw) if isinstance(raw, str) else None
                if value is None:
                    continue
                stamp = _period_key(period)
                if stamp:
                    rows.append((stamp, SCHEDULE, label.strip(), value))

    if not rows:
        return Enrichment(ticker=ticker)

    store.replace_schedule(ticker, rows)

    latest = _latest_by_label(store, ticker)
    result = Enrichment(
        ticker=ticker,
        items=len(rows),
        cash=_roll_up(latest, CASH_ITEMS),
        current_assets=_roll_up(latest, CURRENT_ASSET_ITEMS),
        current_liabilities=_roll_up(latest, CURRENT_LIABILITY_ITEMS),
        long_term_borrowings=_roll_up(latest, LONG_TERM_ITEMS),
    )

    derived = store.valuation_fields(ticker, "derived")
    store.replace_valuations(ticker, "derived", {
        **derived,
        "cash": result.cash,
        "current_assets": result.current_assets,
        "current_liabilities": result.current_liabilities,
        "long_term_borrowings": result.long_term_borrowings,
    })
    # Cash changes enterprise value, which changes the target price and every
    # verdict derived from it, so the company is re-valued rather than left
    # showing figures computed without it.
    result.revalued = revalue(store, ticker)
    return result


def _period_key(label: str) -> str | None:
    """Turn a schedule's ``"Mar 2026"`` heading into an ISO date."""
    from .sources.screener import _month_year

    parsed = _month_year(label)
    return parsed.isoformat() if parsed else None


def _latest_by_label(store: Store, ticker: str) -> dict[str, float]:
    """Newest stored value for each schedule line item, keyed lower-case."""
    rows = store.conn.execute(
        "SELECT label, value FROM financials f WHERE ticker = ? AND statement = ? "
        "AND period = (SELECT MAX(period) FROM financials WHERE ticker = f.ticker "
        "AND statement = f.statement AND label = f.label)",
        (ticker, SCHEDULE),
    )
    return {r["label"].strip().casefold(): r["value"] for r in rows if r["value"] is not None}


def _roll_up(latest: dict[str, float], wanted: tuple[str, ...]) -> float | None:
    total, hit = 0.0, False
    for key, value in latest.items():
        if any(key.startswith(w) for w in wanted):
            total += value
            hit = True
    return total if hit else None
