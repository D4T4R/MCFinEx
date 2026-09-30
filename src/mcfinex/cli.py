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
from . import prices
from .report import screen_all
from .quarters import current_quarter
from .sources import bse, nse, screener

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

    p = sub.add_parser("universe", help="seed tickers and ISINs from the exchange bhavcopies")
    p.add_argument("--limit", type=int, help="only keep the first N listings")
    p.add_argument("--days", type=int, default=7,
                   help="union this many trading sessions (a single day misses "
                        "illiquid stocks that did not trade)")
    p.add_argument("--no-bse", action="store_true",
                   help="NSE only; skip the BSE main board")
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

    p = sub.add_parser("enrich", help="fetch balance-sheet detail for companies")
    p.add_argument("tickers", nargs="*", help="companies to enrich")
    p.add_argument("--missing", type=int, metavar="N",
                   help="instead of named tickers, work through up to N "
                        "companies that have no schedule detail yet")
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

    p = sub.add_parser("publish", help="write the screen as a static JSON site")
    p.add_argument("-o", "--out", default="site",
                   help="directory to write index.json and company/ into")
    p.set_defaults(handler=cmd_publish)

    p = sub.add_parser("notify", help="push what changed to the phone app over FCM")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would be sent, and leave the state untouched")
    p.add_argument("--limit", type=int, default=40,
                   help="never send more than this many in one run")
    p.set_defaults(handler=cmd_notify)

    p = sub.add_parser("prices", help="refresh closing prices from both bhavcopies")
    p.add_argument("--no-revalue", action="store_true",
                   help="update prices without recomputing stored valuations")
    p.add_argument("--no-bse", action="store_true",
                   help="NSE only; do not fill gaps from BSE")
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
    """Seed the tracked companies from the exchanges.

    NSE is the whole universe. BSE contributes only its main board, and only
    companies NSE does not list at all -- matched on ISIN, because a company
    listed on both carries a different symbol on each and matching on the symbol
    would seed it a second time under its BSE name.
    """
    session = requests.Session()
    listings, sessions = nse.universe(days=args.days, session=session)
    if args.limit:
        listings = listings[: args.limit]
    log.info("nse: %d equity listings across %d sessions (%s to %s)",
             len(listings), len(sessions), min(sessions), max(sessions))

    with Store(args.db) as store:
        store.create_schema()
        store.upsert_companies(
            ((l.ticker, {"isin": l.isin, "current_price": l.close}) for l in listings),
            ("isin", "current_price"),
        )
        seeded = len(listings)

        if not args.no_bse:
            added = _seed_bse_main_board(store, listings, args, session)
            seeded += added

    log.info("seeded %d companies into %s", seeded, redact(args.db))
    return 0


def _seed_bse_main_board(store, nse_listings, args, session) -> int:
    """Add BSE main-board companies that NSE does not list.

    Main board only (groups A, B and T). The rest of BSE's exclusive listings are
    SME-platform names, illiquid X/XT stocks and companies flagged non-compliant
    -- thin, irregular filings, which is the input a mechanical screen reads
    worst. `mcfinex.sources.bse.MAIN_BOARD_GROUPS` is where to widen that.
    """
    try:
        candidates, bse_sessions = bse.universe(days=args.days, session=session)
    except (bse.BseError, requests.RequestException) as exc:
        log.warning("bse universe unavailable (%s); seeded from nse alone",
                    type(exc).__name__)
        return 0

    # Already-stored ISINs *and* the ones NSE just supplied: the NSE upsert above
    # may have introduced ISINs this database had never seen, and they are not
    # new BSE companies just because the read happens after the write.
    known = store.known_isins() | {l.isin for l in nse_listings if l.isin}
    fresh = prices.new_listings(known, candidates)
    log.info("bse main board: %d listings across %d sessions, %d not on nse",
             len(candidates), len(bse_sessions), len(fresh))
    if args.limit:
        fresh = fresh[: args.limit]
    if not fresh:
        return 0

    store.upsert_companies(
        ((l.ticker, {"isin": l.isin, "current_price": l.close}) for l in fresh),
        ("isin", "current_price"),
    )
    log.info("seeded %d bse-only companies, e.g. %s", len(fresh),
             ", ".join(l.ticker for l in fresh[:5]))
    return len(fresh)


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
    if not args.tickers and args.missing is None:
        log.error("name some tickers, or use --missing N to work through the backlog")
        return 2

    session = requests.Session()
    pace = screener.Throttle(settings.request_delay)
    done = failed = empty = 0
    with Store(args.db) as store:
        store.create_schema()
        if args.missing is not None:
            targets = store.unenriched_tickers(limit=args.missing)
            outstanding = len(store.unenriched_tickers())
            log.info("%d companies still lack schedule detail; taking %d",
                     outstanding, len(targets))
        else:
            targets = [t.upper() for t in args.tickers]

        for ticker in targets:
            try:
                result = enrich(store, ticker, session=session, throttle=pace)
            except (screener.ScreenerError, requests.RequestException) as exc:
                # One bad company must not end the batch: the run is hours long
                # and the work already committed would be thrown away with it.
                failed += 1
                log.warning("%s failed: %s", ticker, exc)
                continue
            if not result.found_anything:
                # Genuinely has no schedules published -- counted separately so
                # it is not mistaken for a fetch that went wrong.
                empty += 1
                log.warning("%s: no schedules available", ticker)
                continue
            done += 1
            log.info("%s  cash=%s current assets=%s current liabilities=%s%s",
                     ticker, result.cash, result.current_assets,
                     result.current_liabilities, "  (revalued)" if result.revalued else "")
    log.info("enriched %d companies (%d had none published, %d failed)",
             done, empty, failed)
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


def cmd_publish(args) -> int:
    """Build the JSON the phone app reads.

    Deliberately fails rather than publishing an empty screen: the app has no
    way to tell "nothing qualified today" from "the database was unreachable",
    and overwriting a good site with an empty one is worse than not publishing.
    """
    from pathlib import Path

    from .publish import write_site

    with Store(args.db) as store:
        rows = screen_all(store)
        if not rows:
            log.error("nothing screened; refusing to publish an empty site")
            return 1
        written = write_site(store, Path(args.out), rows)
    log.info("published %s", written.summary)
    return 0


def cmd_notify(args) -> int:
    """Send what changed since the last run to the phone.

    The state is saved only when every message was accepted. Advancing it after
    a partial failure would consume the transition -- the next run would see
    nothing new and the alert would be lost rather than delayed.
    """
    from .notify import (
        FcmError, FcmSender, collapse, deliver, evaluate_store, load_credentials,
    )

    with Store(args.db) as store:
        # alert_state arrived after the database did, and the nightly job never
        # runs `init` -- it seeds, prices and publishes against a database that
        # already exists. Without this the first run on any older database, the
        # hosted one included, dies on "no such table". CREATE TABLE IF NOT
        # EXISTS makes it a no-op every night after the first.
        store.create_schema()
        alerts, state = evaluate_store(store)
        # Collapsed before the limit is applied, so `--limit 40` means forty
        # buzzes rather than forty alerts that might be twelve companies.
        pending = collapse(alerts)[: args.limit]

        # Before every branch that writes. A dry run that establishes the
        # baseline is worse than useless: the real run that follows sees no
        # history to compare against and stays silent, so the first night of
        # alerts is lost to the command that was meant to preview it.
        if args.dry_run:
            # Prints rather than logs: this is the output somebody asked for,
            # not a running commentary on a job.
            for item in pending:
                alert = item.alert
                print(f"{alert.trigger.value:<16} {alert.ticker:<12} {alert.headline}")
                print(f"{'':<16} {'':<12} {item.body}")
                print(f"{'':<16} {'':<12} -> {item.condition}")
            print(f"\n{len(pending)} would be sent. State not advanced.")
            return 0

        if not pending:
            # State is still recorded. With nothing sent there is no transition
            # to lose, and this is the write that turns the first run into a
            # baseline rather than a night that pushes every actionable company.
            store.save_alert_state({t: s.as_dict() for t, s in state.items()})
            log.info("nothing to send; state recorded for %d companies", len(state))
            return 0

        try:
            sender = FcmSender(load_credentials())
        except FcmError as exc:
            log.error("%s", exc)
            return 1

        result = deliver(sender, pending)
        if not result.ok:
            log.error("%d of %d failed; not advancing state, so these retry tomorrow",
                      len(result.failed), len(pending))
            return 1

        store.save_alert_state({t: s.as_dict() for t, s in state.items()})
    log.info("sent %d notification(s)", len(result.delivered))
    return 0


def cmd_prices(args) -> int:
    """Overwrite screener's rounded price with the exact exchange close.

    Screener displays the price to the nearest rupee, so a stock closing at
    205.58 is stored as 206. Column AJ drives the current P/E and every target
    price, so the exact figure matters. Prices also move daily while
    fundamentals move quarterly, which is why this is separate from `scrape`.

    Both exchanges, NSE first. A stock that did not trade on NSE that day may
    still have traded on BSE, and one close is better than a fortnight-old one.
    Matching and precedence are :mod:`mcfinex.prices`; BSE is best-effort, since
    a stale price on some names beats no price refresh at all.
    """
    session = requests.Session()
    day, payload = nse.latest_bhavcopy(session=session)
    listings = nse.parse_bhavcopy(payload)
    log.info("nse bhavcopy for %s: %d equity listings", day, len(listings))

    extra: list = []
    if not args.no_bse:
        extra = _bse_listings_for(day, session)

    with Store(args.db) as store:
        store.create_schema()
        _warn_about_duplicate_isins(store)
        merged = prices.merge(listings, extra, store.tickers_by_isin(),
                              known_tickers=set(store.tickers(only_scannable=False)))
        log.info("%s, %d listings matched no tracked company",
                 merged.summary(), merged.unmatched)
        updated = store.update_prices(merged.prices, day)
        log.info("updated %d closing prices", updated)

        if not args.no_revalue:
            revalued = revalue_all(store)
            log.info("recomputed valuations for %d companies", revalued)
    return 0


def _warn_about_duplicate_isins(store) -> None:
    """Say when one security is stored under two tickers.

    Not fatal, and not caused by the second exchange -- the seeder keys on
    ticker, so a company that changes symbol gains a row and keeps the old one.
    But it is worth saying out loud on every run: the abandoned row is still
    screened, on whatever price it held the day the rename happened, and nothing
    else in the pipeline notices.
    """
    duplicates = store.duplicate_isins()
    if not duplicates:
        return
    log.warning("%d isin(s) held by more than one company; neither row can be "
                "matched by isin until reconciled", len(duplicates))
    for isin, tickers in list(duplicates.items())[:10]:
        log.warning("   %s: %s", isin, " and ".join(tickers))


def _bse_listings_for(day: date, session) -> list:
    """BSE's rows for the same session, or none if BSE cannot supply them.

    Deliberately swallowing every failure. NSE has already succeeded by this
    point, so the run has a full set of prices for everything on NSE; letting a
    BSE outage abort it would trade a complete refresh for no refresh. The
    warning says which day went unfilled.

    Only the same session is accepted. BSE's own walk-back would happily return
    Friday for a Monday request, and writing Friday's BSE close under Monday's
    price_date would be worse than leaving the gap -- the staleness would be
    invisible.
    """
    try:
        bse_day, bse_payload = bse.latest_bhavcopy(on=day, session=session)
    except (bse.BseError, requests.RequestException) as exc:
        log.warning("bse bhavcopy unavailable (%s); pricing from nse alone",
                    type(exc).__name__)
        return []
    if bse_day != day:
        log.warning("bse has nothing for %s (newest is %s); pricing from nse alone",
                    day, bse_day)
        return []
    rows = bse.parse_bhavcopy(bse_payload)
    log.info("bse bhavcopy for %s: %d equity listings", bse_day, len(rows))
    return rows


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
