"""Command line entry point: ``python -m mcfinex <command>``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date

import requests

from .config import redact, settings
from .db.store import Store
from .enrich import enrich
from .export import workbook
from .migrate import compare, migrate
from .pipeline import persist, revalue, revalue_all
from .report import screen_all
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
    p.add_argument("--days", type=int, default=7,
                   help="union this many trading sessions (a single day misses "
                        "illiquid stocks that did not trade)")
    p.set_defaults(handler=cmd_universe)

    p = sub.add_parser("scrape", help="scrape companies from screener.in")
    p.add_argument("tickers", nargs="*", help="tickers to scrape; default is the whole universe")
    p.add_argument("--all", action="store_true", help="scrape every stored company")
    p.add_argument("--from-template", action="store_true",
                   help="scrape the companies already listed in the workbook")
    p.add_argument("--template", default=str(settings.template_path))
    p.add_argument("--consolidated", action="store_true", help="prefer consolidated statements")
    p.add_argument("--force", action="store_true", help="re-scrape even if already current")
    p.add_argument("--limit", type=int, help="stop after N companies")
    p.set_defaults(handler=cmd_scrape)

    p = sub.add_parser("enrich", help="fetch balance-sheet detail for named companies")
    p.add_argument("tickers", nargs="+", help="companies to enrich")
    p.set_defaults(handler=cmd_enrich)

    p = sub.add_parser("push", help="copy the local database to a hosted one")
    p.add_argument("--to", required=True,
                   help="target DSN, e.g. postgresql://... (or $MCFINEX_PG)")
    p.add_argument("--all", action="store_true",
                   help="include companies seeded but never scraped")
    p.set_defaults(handler=cmd_push)

    p = sub.add_parser("prune", help="remove ETFs and fund units that are not companies")
    p.add_argument("--apply", action="store_true", help="actually delete; default lists only")
    p.set_defaults(handler=cmd_prune)

    p = sub.add_parser("screen", help="rank companies by BUY signals")
    p.add_argument("-n", "--limit", type=int, default=25)
    p.add_argument("--min-buys", type=int, default=0)
    p.add_argument("--csv", help="write the full screen to this CSV instead of printing")
    p.set_defaults(handler=cmd_screen)

    p = sub.add_parser("prices", help="refresh closing prices from the NSE bhavcopy")
    p.add_argument("--no-revalue", action="store_true",
                   help="update prices without recomputing stored valuations")
    p.set_defaults(handler=cmd_prices)

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
    log.info("schema ready at %s", redact(args.db))
    return 0


def cmd_universe(args) -> int:
    session = requests.Session()
    listings, sessions = nse.universe(days=args.days, session=session)
    if args.limit:
        listings = listings[: args.limit]
    log.info("%d equity listings across %d sessions (%s to %s)",
             len(listings), len(sessions), min(sessions), max(sessions))

    with Store(args.db) as store:
        store.create_schema()
        store.upsert_companies(
            ((l.ticker, {"isin": l.isin, "current_price": l.close}) for l in listings),
            ("isin", "current_price"),
        )
    log.info("seeded %d companies into %s", len(listings), redact(args.db))
    return 0


def cmd_scrape(args) -> int:
    quarter = str(current_quarter())
    session = requests.Session()
    # One throttle for the whole run: a 429 on any company slows every request
    # after it, instead of each ticker rediscovering the limit for itself.
    pace = screener.Throttle(settings.request_delay)
    failures = rate_limited = 0

    with Store(args.db) as store:
        store.create_schema()
        tickers = [t.upper() for t in args.tickers]
        if not tickers and args.from_template:
            tickers = workbook.tickers_in(args.template)
        elif not tickers and args.all:
            tickers = store.tickers()
        if not tickers:
            log.error("no tickers given; pass them explicitly, or use "
                      "--from-template / --all after `universe`")
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
                    throttle=pace,
                )
                company = screener.parse(html, ticker, consolidated=args.consolidated)
                persist(store, company)
                log.info("[%d/%d] %s  %s", index, len(tickers), ticker, company.name)
            except screener.RateLimited as exc:
                # Being throttled says nothing about the company, so keep it
                # distinct from a 404 -- these are worth retrying later.
                failures += 1
                rate_limited += 1
                log.warning("[%d/%d] %s: %s", index, len(tickers), ticker, exc)
            except (screener.ScreenerError, requests.RequestException) as exc:
                # One dead ticker must not end the run; the Java build printed a
                # stack trace and carried on with half-populated state.
                failures += 1
                log.warning("[%d/%d] %s failed: %s", index, len(tickers), ticker, exc)

    if failures:
        log.warning("%d failed (%d rate limited, %d missing). Final delay %.1fs.",
                    failures, rate_limited, failures - rate_limited, pace.delay)
        if rate_limited:
            log.warning("re-run the same command to pick up the throttled ones")
    return 1 if failures and failures == len(tickers) else 0


def cmd_enrich(args) -> int:
    """Pull the schedules behind the collapsed balance-sheet rows.

    Kept out of `scrape` because it is three extra requests per company and
    would roughly double a full-universe run. Enriching re-values the company,
    since cash changes its enterprise value and every target derived from it.
    """
    session = requests.Session()
    pace = screener.Throttle(settings.request_delay)
    done = 0
    with Store(args.db) as store:
        store.create_schema()
        for ticker in (t.upper() for t in args.tickers):
            try:
                result = enrich(store, ticker, session=session, throttle=pace)
            except (screener.ScreenerError, requests.RequestException) as exc:
                log.warning("%s failed: %s", ticker, exc)
                continue
            if not result.found_anything:
                log.warning("%s: no schedules available", ticker)
                continue
            done += 1
            log.info("%s  cash=%s current assets=%s current liabilities=%s%s",
                     ticker, result.cash, result.current_assets,
                     result.current_liabilities, "  (revalued)" if result.revalued else "")
    log.info("enriched %d companies", done)
    return 0


def cmd_push(args) -> int:
    """Replace a hosted database with the local one.

    The deployed app reads whatever this leaves behind, so the copy is a
    replacement rather than a merge: a half-updated screen with no clear as-of
    date is worse than a stale one.
    """
    target_dsn = args.to
    if target_dsn.startswith("$"):
        target_dsn = os.environ.get(target_dsn[1:], "")
    if not target_dsn:
        log.error("no target DSN")
        return 2

    seen: dict[str, int] = {}

    def progress(table: str, copied: int) -> None:
        if copied and copied != seen.get(table):
            seen[table] = copied
            log.info("  %s: %s rows", table, f"{copied:,}")

    with Store(args.db) as source, Store(target_dsn) as target:
        log.info("copying %s -> %s", redact(args.db), target.dialect.name)
        result = migrate(source, target, only_screened=not args.all, progress=progress)
        log.info("copied %s rows", f"{result.total:,}")
        for table, (local, remote) in compare(source, target).items():
            match = "ok" if args.all and local == remote or not args.all else ""
            log.info("  %-16s local %9s  remote %9s %s",
                     table, f"{local:,}", f"{remote:,}", match)
    return 0


def cmd_prune(args) -> int:
    """Drop instruments that arrived via the bhavcopy but are not companies."""
    with Store(args.db) as store:
        store.create_schema()
        targets = store.fund_unit_tickers()
        if not targets:
            log.info("nothing to prune")
            return 0
        if not args.apply:
            log.info("%d fund units would be removed, e.g. %s",
                     len(targets), ", ".join(targets[:6]))
            log.info("re-run with --apply to delete them")
            return 0
        removed = store.remove(targets)
        store.compact()
    log.info("removed %d fund units", removed)
    return 0


def cmd_screen(args) -> int:
    """The workbook's Results sheet, without the workbook."""
    with Store(args.db) as store:
        rows = [r for r in screen_all(store) if r.screening.buy_count >= args.min_buys]
    if not rows:
        log.error("nothing to screen; run `mcfinex scrape` first")
        return 1
    rows.sort(key=lambda r: (-r.screening.buy_count, r.screening.sell_count))

    if args.csv:
        import csv as _csv
        records = [r.as_record() for r in rows]
        with open(args.csv, "w", newline="") as handle:
            writer = _csv.DictWriter(handle, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
        log.info("wrote %s (%d companies)", args.csv, len(records))
        return 0

    print(f"{'TICKER':<12} {'COMPANY':<32} {'BUY':>3} {'SELL':>4} {'PRICE':>10} {'TARGET':>10} {'UPSIDE':>8}")
    for row in rows[: args.limit]:
        s, m = row.screening, row.metrics
        target = f"{row.target_ev_ebitda:,.1f}" if row.target_ev_ebitda else "-"
        upside = f"{m.ev_ebitda_upside:+.1f}%" if m.ev_ebitda_upside is not None else "-"
        price = f"{m.price:,.2f}" if m.price else "-"
        print(f"{s.ticker:<12} {str(s.name)[:32]:<32} {s.buy_count:>3} {s.sell_count:>4} "
              f"{price:>10} {target:>10} {upside:>8}")
    print(f"\n{len(rows)} companies screened. `mcfinex-dashboard` for the full view.")
    return 0


def cmd_prices(args) -> int:
    """Overwrite screener's rounded price with the exact NSE close.

    Screener displays the price to the nearest rupee, so a stock closing at
    205.58 is stored as 206. Column AJ drives the current P/E and every target
    price, so the exact figure matters. Prices also move daily while
    fundamentals move quarterly, which is why this is separate from `scrape`.
    """
    session = requests.Session()
    day, payload = nse.latest_bhavcopy(session=session)
    listings = nse.parse_bhavcopy(payload)
    log.info("bhavcopy for %s: %d equity listings", day, len(listings))

    with Store(args.db) as store:
        store.create_schema()
        updated = store.update_prices({l.ticker: l.close for l in listings}, day)
        log.info("updated %d closing prices", updated)

        if not args.no_revalue:
            revalued = revalue_all(store)
            log.info("recomputed valuations for %d companies", revalued)
    return 0


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
        for model, field, value in store.valuation_rows(ticker):
            shown = "-" if value is None else f"{value:,.3f}"
            print(f"  {model:14} {field:32} {shown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
