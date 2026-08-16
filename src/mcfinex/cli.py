"""Command line entry point: ``python -m mcfinex <command>``."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

import requests

from .config import settings
from .db.store import Store
from .export import workbook
from .pipeline import persist
from .quarters import current_quarter
from .sources import nse, screener

log = logging.getLogger("mcfinex")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(message)s",
    )
    return args.handler(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcfinex",
        description="Scrape Indian company financials from screener.in and export them.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("--db", default=str(settings.db_path), help="SQLite path")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create the database schema")
    p.set_defaults(handler=cmd_init)

    p = sub.add_parser("universe", help="seed tickers and ISINs from the NSE bhavcopy")
    p.add_argument("--limit", type=int, help="only keep the first N listings")
    p.set_defaults(handler=cmd_universe)

    p = sub.add_parser("scrape", help="scrape companies from screener.in")
    p.add_argument("tickers", nargs="*", help="tickers to scrape; default is the whole universe")
    p.add_argument("--all", action="store_true", help="scrape every stored company")
    p.add_argument("--consolidated", action="store_true", help="prefer consolidated statements")
    p.add_argument("--force", action="store_true", help="re-scrape even if already current")
    p.add_argument("--limit", type=int, help="stop after N companies")
    p.set_defaults(handler=cmd_scrape)

    p = sub.add_parser("export", help="fill the SSP workbook's input cells")
    p.add_argument("-t", "--template", default=str(settings.template_path))
    p.add_argument("-o", "--output", default=str(settings.export_path))
    p.add_argument("--in-place", action="store_true",
                   help="write into the template itself instead of a copy")
    p.add_argument("tickers", nargs="*", help="limit to these tickers")
    p.set_defaults(handler=cmd_export)

    p = sub.add_parser("show", help="print one company's stored valuation")
    p.add_argument("ticker")
    p.set_defaults(handler=cmd_show)

    return parser


def cmd_init(args) -> int:
    with Store(args.db) as store:
        store.create_schema()
    log.info("schema ready at %s", args.db)
    return 0


def cmd_universe(args) -> int:
    session = requests.Session()
    day, payload = nse.latest_bhavcopy(session=session)
    listings = nse.parse_bhavcopy(payload)
    if args.limit:
        listings = listings[: args.limit]
    log.info("bhavcopy for %s: %d equity listings", day, len(listings))

    with Store(args.db) as store:
        store.create_schema()
        for listing in listings:
            store.upsert_company(
                listing.ticker,
                {"isin": listing.isin, "current_price": listing.close},
            )
    log.info("seeded %d companies into %s", len(listings), args.db)
    return 0


def cmd_scrape(args) -> int:
    quarter = str(current_quarter())
    session = requests.Session()
    failures = 0

    with Store(args.db) as store:
        store.create_schema()
        tickers = args.tickers or (store.tickers() if args.all else [])
        if not tickers:
            log.error("no tickers given; pass them explicitly or use --all after `universe`")
            return 2
        if args.limit:
            tickers = tickers[: args.limit]

        log.info("scraping %d companies for quarter %s", len(tickers), quarter)
        for index, ticker in enumerate(tickers, start=1):
            if not args.force and not store.needs_refresh(ticker, quarter):
                log.debug("[%d/%d] %s already current", index, len(tickers), ticker)
                continue
            try:
                html = screener.fetch(
                    ticker,
                    consolidated=args.consolidated,
                    session=session,
                    timeout=settings.request_timeout,
                    delay=settings.request_delay,
                )
                company = screener.parse(html, ticker, consolidated=args.consolidated)
                persist(store, company)
                log.info("[%d/%d] %s  %s", index, len(tickers), ticker, company.name)
            except (screener.ScreenerError, requests.RequestException) as exc:
                # One dead ticker must not end the run; the Java build printed a
                # stack trace and carried on with half-populated state.
                failures += 1
                log.warning("[%d/%d] %s failed: %s", index, len(tickers), ticker, exc)

    if failures:
        log.warning("%d company/companies failed", failures)
    return 1 if failures and failures == len(tickers) else 0


def cmd_export(args) -> int:
    tickers = [t.upper() for t in args.tickers] or None
    with Store(args.db) as store:
        path, updated, appended = workbook.populate(
            store, args.template, args.output,
            tickers=tickers, in_place=args.in_place,
        )
    log.info("wrote %s (%d rows updated, %d appended)", path, updated, appended)
    log.info("open it in Excel to recalculate the valuation formulas")
    return 0


def cmd_show(args) -> int:
    ticker = args.ticker.upper()
    with Store(args.db) as store:
        row = store.company(ticker)
        if row is None:
            log.error("%s not in the database", ticker)
            return 1
        print(f"{row['name']}  ({ticker})")
        for key in ("sector", "industry", "current_price", "market_cap", "outstanding_shares",
                    "stock_pe", "latest_period", "last_updated"):
            print(f"  {key:22} {row[key]}")
        for record in store.conn.execute(
            "SELECT model, field, value FROM valuations WHERE ticker = ? ORDER BY model, field",
            (ticker,),
        ):
            value = record["value"]
            shown = "-" if value is None else f"{value:,.3f}"
            print(f"  {record['model']:14} {record['field']:32} {shown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
