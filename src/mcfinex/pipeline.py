"""Turn a scraped screener page into database rows.

Screener publishes raw statement lines; the valuation models want a handful of
derived quantities. Everything derived here is written down explicitly so the
arithmetic is reviewable rather than buried in the scraper.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

from . import labels
from .db.store import Store
from .quarters import current_quarter
from .sources.screener import Company
from .valuation import value_by_eps, value_by_ev_ebitda

# How many periods of history the valuation models consume. Screener usually
# offers 10-12; the original Java was hard-capped at 5 by its column names.
HISTORY = 5


@dataclass
class Derived:
    """Quantities computed from screener's raw lines."""

    outstanding_shares: float | None = None
    enterprise_value: float | None = None
    ev_ebitda_multiple: float | None = None
    ebit: float | None = None
    inventory_turnover: float | None = None
    free_cash_flow: float | None = None
    total_other_liabilities: float | None = None   # includes bank deposits
    deposits: float | None = None


def derive(company: Company) -> Derived:
    """Compute the figures screener does not publish directly."""
    bs = company.section("balance-sheet")
    pl = company.section("profit-loss")
    ratios = company.section("ratios")
    cf = company.section("cash-flow")

    face_value = company.ratios.get("Face Value")
    market_cap = company.ratios.get("Market Cap")
    equity_capital = bs.latest(*labels.EQUITY_CAPITAL)
    borrowings = bs.latest(*labels.BORROWINGS)
    investments = bs.latest(*labels.INVESTMENTS)
    # Banks fund themselves with deposits, which screener lists separately and
    # non-financials do not have at all.
    deposits = bs.latest(*labels.DEPOSITS)

    # Equity capital is share count x face value, both in rupees, so dividing
    # gives the share count. Screener reports the balance sheet in crore, so the
    # result is already in crore -- the unit the Java build stored after its
    # divide-by-10,000,000.
    shares = (
        equity_capital / face_value
        if equity_capital is not None and face_value else None
    )

    # EV = equity + debt - cash. Screener has no cash line in the default view,
    # so Investments stands in for it; that is the same proxy its own ratios use.
    ev = None
    if market_cap is not None:
        ev = market_cap + (borrowings or 0.0) - (investments or 0.0)

    ebitda_latest = pl.latest(*labels.OPERATING_PROFIT)
    multiple = ev / ebitda_latest if ev is not None and ebitda_latest else None

    # EBIT is profit before tax with interest added back.
    pbt = pl.latest(*labels.PROFIT_BEFORE_TAX)
    interest = pl.latest(*labels.INTEREST)
    ebit = pbt + interest if pbt is not None and interest is not None else None

    inv_days = ratios.latest(*labels.INVENTORY_DAYS)
    turnover = 365.0 / inv_days if inv_days else None

    other_liabilities = bs.latest(*labels.OTHER_LIABILITIES)

    return Derived(
        outstanding_shares=shares,
        enterprise_value=ev,
        ev_ebitda_multiple=multiple,
        ebit=ebit,
        inventory_turnover=turnover,
        free_cash_flow=cf.latest(*labels.FREE_CASH_FLOW),
        # Deposits belong with the other liabilities so that
        # reserves + capital + liabilities + borrowings reconciles to the
        # balance-sheet total for banks as well as for everyone else.
        total_other_liabilities=_add(other_liabilities, deposits),
        deposits=deposits,
    )


def _add(*values: float | None) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def company_fields(company: Company, derived: Derived, *, today: date | None = None) -> dict:
    """The ``companies`` row for a scrape."""
    today = today or date.today()
    industry = company.industry + [None, None, None]
    return {
        "name": company.name,
        "sector": industry[0],
        "broad_industry": industry[1],
        "industry": industry[2],
        "face_value": company.ratios.get("Face Value"),
        "market_cap": company.ratios.get("Market Cap"),
        "current_price": company.ratios.get("Current Price"),
        "book_value": company.ratios.get("Book Value"),
        "stock_pe": company.ratios.get("Stock P/E"),
        "dividend_yield": company.ratios.get("Dividend Yield"),
        "roce": company.ratios.get("ROCE"),
        "roe": company.ratios.get("ROE"),
        "outstanding_shares": derived.outstanding_shares,
        "consolidated": company.consolidated,
        "last_updated": today.isoformat(),
        "last_updated_quarter": str(current_quarter(today)),
        "latest_period": company.latest_period,
    }


def financial_rows(company: Company) -> list[tuple[str, str, str, float | None]]:
    """Flatten every section into ``(period, statement, label, value)`` rows."""
    rows: list[tuple[str, str, str, float | None]] = []
    for statement, section in company.sections.items():
        for label, values in section.rows.items():
            clean_label = label.rstrip("+ ").strip()
            for period, value in zip(section.periods, values):
                if period is None or value is None:
                    continue
                rows.append((period.isoformat(), statement, clean_label, value))
    return rows


def valuations(company: Company, derived: Derived) -> dict[str, dict]:
    """Run all three valuation models over the scraped history."""
    pl = company.section("profit-loss")
    quarters = company.section("quarters")

    ebitda = pl.series(*labels.OPERATING_PROFIT)[-HISTORY:]
    eps_yearly = pl.series(*labels.EPS)[-HISTORY:]
    eps_quarterly = quarters.series(*labels.EPS)[-HISTORY:]
    price = company.ratios.get("Current Price")

    ev_model = value_by_ev_ebitda(
        ebitda,
        ev_ebitda_multiple=derived.ev_ebitda_multiple,
        outstanding_shares=derived.outstanding_shares,
        long_term_borrowings=company.section("balance-sheet").latest(*labels.BORROWINGS),
        current_price=price,
    )
    return {
        "ev_ebitda": ev_model.as_dict(),
        "eps_yearly": value_by_eps(eps_yearly, current_price=price).as_dict(),
        "eps_quarterly": value_by_eps(eps_quarterly, current_price=price).as_dict(),
    }


def persist(store: Store, company: Company, *, today: date | None = None) -> Derived:
    """Derive, value and write a scraped company in one go."""
    derived = derive(company)
    store.upsert_company(company.ticker, company_fields(company, derived, today=today))
    store.replace_financials(company.ticker, financial_rows(company))
    for model, fields in valuations(company, derived).items():
        store.replace_valuations(company.ticker, model, fields)
    # Derived inputs are stored as their own "model" so the workbook export can
    # read them back without widening the companies table for each new figure.
    store.replace_valuations(company.ticker, "derived", asdict(derived))
    return derived
