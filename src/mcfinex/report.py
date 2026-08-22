"""Assemble screening inputs from the database and run the models.

Sits between :mod:`mcfinex.db.store` and the pure :mod:`mcfinex.screening`
layer, so neither has to know about the other. This is what the CLI and the
Streamlit app both call.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from . import labels
from .db.store import Store
from .picks import classify
from .screening import Metrics, Screening, Verdict, screen

BALANCE_SHEET = "balance-sheet"
RATIOS = "ratios"

#: A sector needs at least this many companies before its median P/E is
#: trustworthy enough to judge a constituent against.
MIN_SECTOR_SAMPLE = 3


def sector_pe_medians(store: Store, *, min_sample: int = MIN_SECTOR_SAMPLE) -> dict[str, float]:
    """Median P/E per sector, computed from what has been scraped.

    Screener does not publish an industry P/E on the company page -- the peers
    table is loaded separately -- so the comparison the workbook wanted is
    rebuilt from our own universe instead of costing a request per company.
    Loss-making companies are excluded; a negative P/E is not a cheap one.
    """
    buckets: dict[str, list[float]] = {}
    for industry, sector, stock_pe in store.sector_pe_rows():
        key = industry or sector
        if key:
            buckets.setdefault(key, []).append(stock_pe)
    return {
        sector: statistics.median(values)
        for sector, values in buckets.items()
        if len(values) >= min_sample
    }


def metrics_for(store: Store, ticker: str, sector_medians: dict[str, float]) -> Metrics | None:
    """Gather one company's screening inputs."""
    company = store.company(ticker)
    if company is None:
        return None

    def latest(statement: str, *names: str) -> float | None:
        values = store.series(ticker, statement, *names, limit=1)
        return values[0] if values else None

    inventory = store.series(ticker, RATIOS, *labels.INVENTORY_DAYS, limit=2)
    quarters_reported = len(store.series(ticker, "quarters", *labels.EPS))
    ev = store.valuation_fields(ticker, "ev_ebitda")
    eps_yearly = store.valuation_fields(ticker, "eps_yearly")
    eps_quarterly = store.valuation_fields(ticker, "eps_quarterly")
    derived = store.valuation_fields(ticker, "derived")

    sector = company["industry"] or company["sector"]
    # Screener only writes "Financing Profit" for banks, NBFCs and insurers.
    is_financial = bool(store.series(ticker, "profit-loss", "Financing Profit", limit=1))
    other_liabilities = latest(BALANCE_SHEET, *labels.OTHER_LIABILITIES)
    deposits = latest(BALANCE_SHEET, *labels.DEPOSITS)

    return Metrics(
        ticker=ticker,
        name=company["name"],
        sector=sector,
        price=company["current_price"],
        reserves=latest(BALANCE_SHEET, *labels.RESERVES),
        equity_capital=latest(BALANCE_SHEET, *labels.EQUITY_CAPITAL),
        other_liabilities=_sum(other_liabilities, deposits),
        borrowings=latest(BALANCE_SHEET, *labels.BORROWINGS),
        roce=company["roce"],
        inventory_days=inventory[0] if inventory else None,
        inventory_days_prior=inventory[1] if len(inventory) > 1 else None,
        free_cash_flow=derived.get("free_cash_flow"),
        stock_pe=company["stock_pe"],
        sector_pe=sector_medians.get(sector) if sector else None,
        book_value=company["book_value"],
        dividend_yield=company["dividend_yield"],
        promoter_holding=latest("shareholding", *labels.PROMOTERS),
        is_financial=is_financial,
        quarters_reported=quarters_reported,
        current_assets=derived.get("current_assets"),
        current_liabilities=derived.get("current_liabilities"),
        ev_ebitda_upside=ev.get("difference_pct"),
        ev_ebitda_upside_with_debt=ev.get("difference_with_borrowing_pct"),
        pe_yearly_rerating=eps_yearly.get("difference_in_pe_pct"),
        pe_quarterly_rerating=eps_quarterly.get("difference_in_pe_pct"),
    )


@dataclass
class Row:
    """One screened company, plus the numbers the dashboard displays."""

    screening: Screening
    metrics: Metrics
    target_ev_ebitda: float | None = None
    target_pe_yearly: float | None = None
    target_pe_quarterly: float | None = None
    entry_3by4: float | None = None
    entry_2by3: float | None = None

    def as_record(self) -> dict:
        """Flatten to one dict per company, for a table."""
        m, s = self.metrics, self.screening
        record = {
            "Ticker": s.ticker,
            "Company": s.name,
            "Sector": s.sector,
            "Price": m.price,
            "BUY signals": s.buy_count,
            "SELL signals": s.sell_count,
            "Scored": s.scored_count,
            "Tier": classify(self).value,
            "Quality BUY": s.quality_buy_count,
            "EV/EBITDA target": self.target_ev_ebitda,
            "Upside %": m.ev_ebitda_upside,
            "Entry 3/4": self.entry_3by4,
            "Entry 2/3": self.entry_2by3,
            "PE yearly target": self.target_pe_yearly,
            "PE quarterly target": self.target_pe_quarterly,
        }
        for signal in s.signals:
            record[signal.label] = signal.verdict.value
        return record


#: Every line item a screen reads, grouped by statement, so the whole universe
#: can be fetched in one query rather than one per company per label.
SCREEN_LINES: dict[str, list[str]] = {
    BALANCE_SHEET: list(dict.fromkeys(
        labels.RESERVES + labels.EQUITY_CAPITAL + labels.OTHER_LIABILITIES
        + labels.BORROWINGS + labels.DEPOSITS
    )),
    "shareholding": list(labels.PROMOTERS),
    RATIOS: list(labels.INVENTORY_DAYS),
    "quarters": list(labels.EPS),
    "profit-loss": list(dict.fromkeys(labels.EPS + ("Financing Profit",))),
}


def screen_all(store: Store, tickers: list[str] | None = None) -> list[Row]:
    """Screen every scraped company, or a chosen subset.

    Loads the whole universe in three queries and assembles in memory. Doing it
    per company issued 43,398 queries for a full screen -- unnoticeable against
    a local file, but eleven minutes of round-trips against a hosted database.
    """
    medians = sector_pe_medians(store)
    companies = store.all_companies()
    series = store.all_series(SCREEN_LINES)
    valuations = store.all_valuations()

    wanted = tickers if tickers is not None else list(companies)
    rows: list[Row] = []
    for ticker in wanted:
        company = companies.get(ticker)
        if company is None:
            continue
        models = valuations.get(ticker, {})
        metrics = _metrics_from(company, series.get(ticker, {}), models, medians)
        ev = models.get("ev_ebitda", {})
        rows.append(Row(
            screening=screen(metrics),
            metrics=metrics,
            target_ev_ebitda=ev.get("target_price"),
            target_pe_yearly=models.get("eps_yearly", {}).get("target_price"),
            target_pe_quarterly=models.get("eps_quarterly", {}).get("target_price"),
            entry_3by4=ev.get("entry_price_1by4"),
            entry_2by3=ev.get("entry_price_1by3"),
        ))
    return rows


def _metrics_from(company, series: dict, models: dict, medians: dict[str, float]) -> Metrics:
    """Build Metrics from already-loaded rows, with no further queries."""

    def first(statement: str, *names: str) -> float | None:
        for name in names:
            values = series.get((statement, name))
            if values:
                return values[0]
        return None

    def run(statement: str, *names: str) -> list[float]:
        for name in names:
            values = series.get((statement, name))
            if values:
                return values
        return []

    ev = models.get("ev_ebitda", {})
    derived = models.get("derived", {})
    sector = company["industry"] or company["sector"]
    inventory = run(RATIOS, *labels.INVENTORY_DAYS)

    return Metrics(
        ticker=company["ticker"],
        name=company["name"],
        sector=sector,
        price=company["current_price"],
        reserves=first(BALANCE_SHEET, *labels.RESERVES),
        equity_capital=first(BALANCE_SHEET, *labels.EQUITY_CAPITAL),
        other_liabilities=_sum(first(BALANCE_SHEET, *labels.OTHER_LIABILITIES),
                               first(BALANCE_SHEET, *labels.DEPOSITS)),
        borrowings=first(BALANCE_SHEET, *labels.BORROWINGS),
        roce=company["roce"],
        inventory_days=inventory[0] if inventory else None,
        inventory_days_prior=inventory[1] if len(inventory) > 1 else None,
        free_cash_flow=derived.get("free_cash_flow"),
        stock_pe=company["stock_pe"],
        sector_pe=medians.get(sector) if sector else None,
        book_value=company["book_value"],
        dividend_yield=company["dividend_yield"],
        promoter_holding=first("shareholding", *labels.PROMOTERS),
        # Screener only writes "Financing Profit" for banks, NBFCs and insurers.
        is_financial=bool(series.get(("profit-loss", "Financing Profit"))),
        quarters_reported=len(run("quarters", *labels.EPS)),
        current_assets=derived.get("current_assets"),
        current_liabilities=derived.get("current_liabilities"),
        ev_ebitda_upside=ev.get("difference_pct"),
        ev_ebitda_upside_with_debt=ev.get("difference_with_borrowing_pct"),
        pe_yearly_rerating=models.get("eps_yearly", {}).get("difference_in_pe_pct"),
        pe_quarterly_rerating=models.get("eps_quarterly", {}).get("difference_in_pe_pct"),
    )


def _sum(*values: float | None) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) if present else None
