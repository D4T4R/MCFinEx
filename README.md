# MCFinEx

Scrapes Indian company financials from [screener.in](https://www.screener.in) into
SQLite, then fills the input cells of the SSP valuation workbook.

A Python rewrite of the Java/Selenium MCFinEx, which scraped moneycontrol.com by
absolute XPath. Those XPaths no longer resolve, and neither does the NSE URL the
old bhavcopy loader used.

> **Not investment advice.** Every signal here is produced by a mechanical model
> over publicly filed financials. The thresholds that turn a number into BUY,
> HOLD or SELL were chosen by hand — a different set, on the same data, would
> give different verdicts. The operator is not a SEBI-registered investment
> adviser and offers no personalised advice. Do your own research, and consult
> a registered adviser before making any investment decision. The full text is
> in `src/mcfinex/disclaimer.py` and is shown on every page of the app.

## How it works

```
NSE bhavcopy ──> tickers + ISINs ──┐
BSE bhavcopy ──> the gaps NSE left ┤──> SQLite ──> SSP workbook (Excel computes)
screener.in ──> financial history ─┘
```

**Python owns the model.** Scoring lives in `screening.py`, which is pure — no
database, no UI, no I/O — so the CLI, the Streamlit app and anything added later
all share one definition of every threshold.

The SSP workbook is now an optional export, not the engine. It used to hold the
valuation as Excel formulas, but its STRATEGY columns all pointed at a deleted
lookup table and evaluated to `#REF!`; the only surviving trace of their
vocabulary was `Results!M = COUNTIF(C:L,"BUY")`. Thresholds were recovered from
the surviving `IF` conditions and restated in Python.

## Install

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

## Use

```bash
mcfinex init                          # create the SQLite schema
mcfinex universe                      # seed ~2550 companies + ISINs (unions 7 sessions)
mcfinex prune --apply                 # drop ETFs and fund units (not companies)
mcfinex renames                       # companies the exchange has renamed (--apply to move)
mcfinex scrape RELIANCE TCS           # scrape named companies
mcfinex scrape --from-template        # scrape the companies tracked in the workbook
mcfinex scrape --all --limit 50       # or work through the seeded universe
mcfinex prices                        # refresh closing prices from both bhavcopies
mcfinex enrich RELIANCE TCS           # pull balance-sheet detail for named companies
mcfinex show RELIANCE                 # print stored values and valuations
mcfinex screen --min-buys 6           # rank by BUY signals
mcfinex screen --csv screen.csv       # or dump the whole screen
mcfinex publish --out site            # static JSON the phone app reads
mcfinex notify --dry-run              # what tonight would push to the phone
mcfinex-dashboard                     # two-page Streamlit UI on :8501
mcfinex-api                           # read-only JSON API on :8000
mcfinex export                        # optional: fill a copy of the workbook
```

Re-running `scrape` skips anything already checked today or already carrying the
current quarter's results; `--force` overrides that.

`universe` unions several trading sessions on purpose. A bhavcopy lists only
what traded that day, so one file undercounts: 2026-08-14 held 2,713 equity
listings where a week unioned held 2,867. The 154 missing were illiquid small
caps that had simply not traded, not new listings.

Screener rate-limits. `scrape` shares one adaptive throttle across the run: a
429 doubles the delay for every request that follows and is retried with
backoff, honouring `Retry-After`. Don't set `MCFINEX_REQUEST_DELAY` below 1.0 —
a full-universe run at 0.7s was throttled from its 71st company and lost 716 of
2,258. Rate-limited companies are reported separately from missing ones; re-run
the same command to pick them up.

Run `prices` after `scrape`. Screener displays the price rounded to the nearest
rupee — a stock closing at 205.58 is shown as 206 — and column AJ drives the
current P/E and every target price, so `prices` overwrites it with the exact
exchange close and recomputes the stored valuations. It is one download rather
than one request per company, so it is quick enough to run daily: prices move
daily, fundamentals quarterly.

## Two exchanges

Both bhavcopies, NSE first. A stock that did not trade on NSE on a given day may
still have traded on BSE, and one close beats a fortnight-old one — on
2026-09-30 that was 6 companies. `--no-bse` on either command turns it off.

**Matched on ISIN, never on symbol.** A company listed on both carries a
different ticker on each, so matching BSE rows on `TckrSymb` would miss the ones
that differ and, worse, occasionally hit a *different* company that happens to
share a symbol. NSE rows keep matching on ticker, because every stored ticker is
an NSE symbol and 21 companies have no ISIN recorded at all.

**NSE wins where both traded**, which is nearly always: 2,395 of 2,543 NSE
listings also traded on BSE. That is the "don't price the same stock twice" case
and it is the common one.

BSE is best-effort. NSE has already succeeded by the time it is asked, so an
outage there costs the BSE rows rather than the run. A BSE file for a *different*
session is refused outright — writing Friday's close under Monday's `price_date`
would make the staleness invisible.

Two BSE quirks are load-bearing, both verified against the live feed:

- It answers a request for a non-trading day with **HTTP 200 and a 14 KB HTML
  page**, not a 404. Parsed as CSV that yields no rows, and since a price update
  only touches companies it already knows, no rows writes nothing and reports
  success. So the body is checked, not the status code.
- Only the bare `.CSV` works; the sibling `.csv.zip` serves the same HTML page
  even on trading days.

Its `SctySrs` column is the same field with a different vocabulary — a dozen
group codes rather than NSE's `EQ`/`BE` — and series `F` carries bonds with `INE`
ISINs, so the ISIN prefix that separates equity from fund units on NSE does not
separate equity from debt here. The group is what decides.

## Renamed companies

The exchange renames companies, and the seeder keys on ticker, so a rename used
to fork one company into two rows: an empty one under the new symbol, and the
old one holding every quarter of history while dropping out of the price feed.
The orphan then went on being screened forever, on whatever price it held the
day the rename landed — and since both rows looked ordinary, nothing noticed.

`universe` now follows the rename before seeding, moving the company and its
history across all four ticker-keyed tables. `mcfinex renames` shows what would
move; `--apply` does it. `--no-renames` on `universe` opts out.

The inference is deliberately narrow: a rename is only taken when the feed lists
the ISIN under some symbol **and the stored symbol is absent from both feeds
entirely**. A stored symbol that is still trading is never treated as an old
name, whatever shares its ISIN — merging two live companies would destroy a
history that no later run could rebuild. Anything less certain is reported and
left alone.

Seeding takes only BSE's **main board** (groups A, B, T). Of 2,032 BSE-only
listings on 2026-09-07, 1,474 were illiquid X/XT names, 386 were SME-platform
and 82 were flagged non-compliant; median turnover across the lot was ₹1.5 lakh
for the day. Their filings are thin and irregular, which is the input a
mechanical screen reads worst. `sources/bse.py` is where to widen that.

Settings are environment variables, all optional:

| Variable | Default |
|---|---|
| `MCFINEX_DB` | `data/stocks.db` |
| `MCFINEX_TEMPLATE` | `~/Downloads/SSP_Working_merged.xlsx` |
| `MCFINEX_EXPORT` | `data/SSP_Working_populated.xlsx` |
| `MCFINEX_REQUEST_DELAY` | `1.0` seconds between screener requests (see below) |

## Scheduled jobs

| Workflow | When | What |
|---|---|---|
| `nightly-prices` | 18:30 IST, weekdays | Closing prices and newly listed symbols |
| `quarterly-fundamentals` | 15 Feb, May, Aug, Nov | Full re-scrape after results season |

Indian companies have 45 days from a quarter's end to file, so the quarterly
run is timed to land after most have reported. The May run also covers the
annual report, for which SEBI allows 60 days — a few names file late enough to
miss it, so a manual re-run at the end of May is worth doing.

A full scrape is ~2,500 companies at 1.5s each, so 60–90 minutes. It re-scrapes
only what is not already carrying the current quarter's results, so a re-run
after a failure resumes rather than starting over.

## The two pages

`Ideas` narrows; `Detailed screen` explores.

The landing page shows cards, not a table, because 74% of the universe reports
some EV/EBITDA upside — the model projects historical EBITDA growth and most
companies have had some, so "cheap" on that measure alone selects almost
nothing out. A pick therefore has to clear the workbook's own discipline and be
corroborated:

| Tier | Rule | Roughly |
|---|---|---|
| High conviction | Below the 2/3 entry price, 6+ BUY signals, all three models agreeing, excluding new listings and financials | 127 |
| Below entry price | Below the 3/4 entry price | 1,031 |
| Watch | Over 25% upside on EV/EBITDA alone | 305 |

Ranked by corroboration before size of upside: a 400% upside on one model with
two BUY signals is noise, and sorting on upside would put it top. Cards carry
their own caveats — newly listed, financial, not enriched, thin history,
implausible upside — so a number never travels without its qualification.

**Sector heat** counts high-conviction names only. Counting everything below an
entry price would cover 45% of the market, so a sector at 90% would sit barely
above the base rate; high conviction is ~5%, so anything well above that means
something.

The detail page adds an **eight-quarter trend** per company with a projection.
Growth there is year on year, each quarter against the same quarter twelve
months earlier, because quarterly results are seasonal — the workbook's
quarter-on-quarter comparison measured the calendar as much as the business.
The projection is a seasonal naive forecast with drift, labelled with a
confidence derived from how stable that growth has been. It is arithmetic on
published figures, not a prediction.

### Filtering by measure and verdict

The detail page's sidebar carries a **Measure** and a **Verdict** selector that
work together, the way a BI tool composes metrics and filters. Choosing a
measure narrows the columns to that signal and whatever numbers belong with it
— selecting EV/EBITDA also brings its target and both entry prices. Choosing a
verdict narrows the rows to companies the selected measures rate that way.

With more than one measure chosen, a **Match** control decides whether a company
must satisfy any of them or all. Across the current universe that is the
difference between 1,967 companies (`Price / book` or `ROCE %` reading SELL) and
289 (both reading SELL).

A verdict on its own does nothing, since there is no measure for it to apply to;
the sidebar says so rather than silently ignoring it.

### Why a signal reads the way it does

Selecting any signal on the detail page opens its working: the inputs, the
formula, the arithmetic, the threshold it crossed, and what the measure means.
HBL Engineering's Price / book, for instance, resolves to `670.05 / 78.40 =
8.55`, and 8.55 is above the 3 SELL line.

Definitions are written in `screening.py` rather than quoted, with a link out
to Investopedia for further reading. That link is a search URL, not a deep one:
Investopedia blocks automated requests, so a `/terms/...` path cannot be
verified from here, and a search that always resolves beats a deep link that
might 404.

## API

`mcfinex-api` serves the same screening over HTTP, read-only, from the database
alone — a page load never triggers a scrape.

| Endpoint | Returns |
|---|---|
| `GET /summary` | universe size, tier counts, price and scrape dates |
| `GET /picks?tier=&sector=&limit=` | ranked candidates |
| `GET /sectors` | where conviction is clustering |
| `GET /company/{ticker}` | signals, targets and eight-quarter trends |
| `GET /health` | database path and company count |

The landing page calls the Python layer directly rather than over HTTP, so the
Streamlit route needs only one process. The API exists so a React front end can
replace `Ideas` without any screening logic moving: `screening.py`, `picks.py`
and `trends.py` import no framework, and a test asserts they never will.

## Published data

`mcfinex publish` writes the same screening as static JSON, which is what the
phone app reads. The data changes once a night and the audience is a handful of
people, so a hosted API would be a process to keep alive and a cold start to
wait through in exchange for answering the same question all day.

| File | Holds | Over the wire |
|---|---|---|
| `index.json` | counts, sector heat, every ranked pick | ~99 KB gzipped |
| `company/{id}.json` | signals, targets, eight-quarter trends | ~1.4 KB gzipped |

`id` is the ticker with `&` mapped to `_` (`M&M` → `M_M.json`), and travels with
each pick so a client never reimplements the rule. `_` never occurs in an NSE
ticker, which makes the mapping injective; `publish` fails rather than deploy if
two tickers ever collide.

Every payload carries `"schema"`. The app is sideloaded, so no update can be
forced on anyone — an old build has to be able to recognise a payload it cannot
read and say so, rather than display nonsense.

Deployed to GitHub Pages at the end of the nightly refresh, in the same job that
wrote the prices. A separate scheduled workflow would have to guess how long the
refresh takes, and GitHub's scheduler has lagged three hours here.

The API and the published files share their serialisers (`publish.py`), so the
two cannot drift into disagreeing about a field.

## Alerts

`mcfinex notify` runs after the deploy and pushes what *changed* — a rule that
matched on state alone would resend the same six hundred companies every night
until the reader muted it. `alerts.py` holds the rules and stays pure; `notify.py`
puts them on the wire.

Delivery is by FCM **topic**, so the phone subscribes itself. There is no device
table, no write endpoint and no record of who runs the app — and no way to send
to one person, which is the cost of that.

Three properties are load-bearing, and each has a test that fails without it:

- The **first run is silent**. It records a baseline instead, or night one pushes
  every company already sitting below its entry price.
- **State advances only on a successful send.** Advancing it after a failure
  consumes the transition, so the alert would be lost rather than delayed.
- **One company, one notification.** The triggers are correlated — a company that
  falls to its entry price has usually just entered the top tier too — so each
  push goes to a *condition* covering everyone with a reason to hear it, rather
  than three messages to overlapping topics.

Set-up is `MCFINEX_FCM` (a Firebase service account JSON) as a repository secret;
see `app/README.md`. Without it the nightly job skips the send and stays green.

## Workbook column map

Rows 1–3 are headers; data starts at row 4, keyed by ticker in column **B**.
Series columns run **newest first** — `CD` is Y5 and `CP = CO/CD` is the current
P/E, so Y5/Q5 are the latest period.

A column this tool owns is always written **or blanked** — never left holding a
previous value. A row ends up wholly current or empty, never current in one cell
and years old in the next. Columns with no screener source (promoter pledge `E`,
industry P/E `AM`, current assets `R`, current liabilities `S`) are never passed
to the writer, so whatever they already hold survives.

| Cells | Written |
|---|---|
| C, DQ | company name, ticker |
| D | promoter holding % |
| H, I, M, W | reserves, equity capital, other liabilities (incl. deposits), total liabilities |
| N | borrowings — see note below |
| V, AA | EBIT (PBT + interest), inventory turnover (365 / inventory days) |
| AD–AF, AH | operating / investing / financing / free cash flow |
| AJ, AK, AP, AS | price (exact NSE close), TTM EPS, book value, dividend yield |
| AZ | current EV/EBITDA multiple (feeds `BP = AZ*BO`) |
| BE–BI | EBITDA, 5 periods |
| BQ, BS | long-term borrowings, shares outstanding (crore) |
| CD–CH | yearly EPS, Y5→Y1 |
| CU–CY | quarterly EPS, Q5→Q1 |
| DM–DP | market cap, sector, last quarter, last checked |

Cells not listed — every formula, and every `STRATEGY`/`REMARK` column — are left
untouched. `AU–AY` and `BA–BD` are cleared: they held the MoneyControl enterprise
values that fed `BE=AU/AZ`, but EBITDA now goes straight into `BE–BI`, leaving
that range orphaned and full of `-8888888` sentinels.

### Banks and NBFCs

Screener renders financial companies with a different vocabulary, and looking a
line up by one exact spelling silently finds nothing for about 8% of listed
companies — which, combined with skip-on-missing, used to leave those cells
holding 2022 data. All lookups go through `labels.py`:

| Ordinary company | Bank / NBFC |
|---|---|
| `Sales` | `Revenue` |
| `Operating Profit` | `Financing Profit` |
| `Borrowings` | `Borrowing` |
| — | `Deposits` (folded into M) |

Deposits are a bank's principal liability; without them the balance sheet fails
to reconcile by the entire deposit base. With the mapping in place all 630
scraped companies satisfy `H+I+M+N = W`.

## Corrections to the original

Ported behaviour, except where the Java was demonstrably wrong:

- **Sentinel values.** `getElementValuebyXpath` returned `-8888888`, `-777777`,
  `-9999999` and `-5555555` on failure, and those went through `Float.parseFloat`
  into numeric columns. They are still sitting in the shipped workbook. Missing
  data is now `NULL`, and the exporter clears an owned cell it cannot fill
  rather than leaving a sentinel beside current data.
- **SQL injection.** `DBUtils.convertMapToSQL` concatenated scraped strings into
  `MERGE INTO ... VALUES ('...')`. Any apostrophe broke the query; anything else
  ran as SQL. All writes are parameterised.
- **Sign error.** `computeEV2EBITDAValuation` computed
  `(borrowings - forecastEV) / shares`, so every company with debt got a negative
  target price. Equity value is EV *minus* net debt.
- **Unit mismatch.** EV/EBITDA growth was a fraction but was then divided by 100
  again (`fEBITDA1 * growth / 100`), making it 100× too small; EPS growth was a
  percentage used without dividing, making it 100× too large. Growth is a
  fraction everywhere here until a field is named `_pct`.
- **EPS ordering.** `computeEPSValuation` used `Y_EPS_5` as the current EPS while
  the scraper wrote the *newest* value to `Y_EPS_1`, so current P/E was computed
  from the oldest year. Verified against the workbook, where `CP = CO/CD` uses
  the newest. Our computed current P/E now reconciles with screener's own
  reported Stock P/E.
- **Unbounded retry.** `downloadFileHttp` looped `while (!bGotFile)` with no
  limit, spinning forever once the URL 404'd. Lookback is now capped.
- **Row skipping.** `populateData()` called `iList.next()` twice per iteration,
  silently dropping every other row.
- **Dead endpoints.** The NSE path (`archives.nseindia.com/content/historical/…`)
  now 404s for every date; replaced with the current UDiFF feed. The MoneyControl
  XPaths are replaced by screener's labelled rows, which do not depend on a row's
  position in the table.
- **Hardcoded paths.** `E:\Selenium\chromedriver.exe`, `E://StockData.csv` and
  `jdbc:h2:tcp://localhost/~/stockDB` with `sa`/`sa` are gone.

## On-demand detail

The company page collapses detail into `Other Assets`, `Other Liabilities` and
`Borrowings`. Screener expands them through an undocumented JSON endpoint,
`/api/company/{id}/schedules/`, which supplies three things the page does not:

| | Why it matters |
|---|---|
| Cash equivalents | Enterprise value can net off cash instead of being `market cap + debt` |
| Current assets and liabilities | The current ratio becomes computable at all |
| Long vs short term borrowings | The long-term figure stops being total debt |

Three extra requests per company, so it is deliberately not part of `scrape` —
it would roughly double a full-universe run for data most screens never read.
Run `mcfinex enrich TICKER`, or use the button in the dashboard's company
detail. Because cash changes enterprise value, enriching re-values the company
and re-scores every verdict derived from it.

MoneyControl was considered as the fallback and rejected: its financial tables
are client-rendered, with no API in the page, so it would need a headless
browser — exactly what this rewrite removed. The only figure it still has that
screener lacks is promoter pledge, for which promoter holding stands in.

## Known gaps

- **Column N is repurposed.** Screener's balance sheet does not split current
  from non-current — its `Other Liabilities` already covers both, and lands in M.
  N therefore carries **Borrowings**, which makes `P = (M+N)/O` a correct
  debt-to-equity ratio, matching that column group's own title. The `CURRENT
  LIABILITY` header is left stale rather than editing the template.
- **Current assets and liabilities** are only available after `mcfinex enrich`
  has fetched that company's schedules. Until then the current ratio reports
  UNKNOWN rather than a guess.
- **The workbook holds ~1,970 rows.** Its formulas stop there, so a full-universe
  export skips the overflow and says so rather than writing inputs into rows that
  compute nothing. Everything is in the database and the dashboard regardless.
- **Recently listed companies** have their P/E re-rating signals withheld. An
  IPO multiplies the share count, so pre-listing EPS is not comparable with
  post-listing EPS: Milky Mist's yearly series reads 90.71 then 0.69, which the
  model would otherwise score as a collapse. Detected by having no quarterly
  results yet. EV/EBITDA still applies, since EBITDA is an absolute figure
  rather than a per-share one. New listings arrive on their own through
  `universe`, which reads each day's bhavcopy.
- **Industry P/E (AM)** is not on the screener company page — the peers table is
  loaded separately. The column is left at whatever it already held.
- **Enterprise value history (AU–AY) and EV/EBITDA history (AZ–BD)** are not
  published by screener. EBITDA is taken directly from Operating Profit into
  BE–BI instead, which is the quantity `BE=AU/AZ` was reconstructing anyway. Only
  AZ is written, because `BP = AZ*BO` needs it.
- Quarterly EPS growth is quarter-over-quarter and therefore seasonal; the model
  is inherited from the workbook, not endorsed.

## Layout

```
src/mcfinex/
  cli.py             argparse entry point
  config.py          env-var settings
  pipeline.py        scrape -> derive -> value -> store
  quarters.py        Indian fiscal-quarter arithmetic
  valuation.py       EV/EBITDA and EPS models (pure functions)
  db/schema.sql      SQLite schema
  db/store.py        parameterised persistence
  labels.py          screener line-item names and bank/NBFC aliases
  screening.py       BUY/HOLD/SELL signals (pure: no DB, no UI)
  picks.py           tiering, ranking and sector heat (pure)
  trends.py          eight-quarter analysis and projection (pure)
  report.py          store -> screening glue, sector median P/E
  alerts.py          transition-only alert rules (pure)
  api.py             read-only FastAPI over the database
  publish.py         static JSON site; shares its serialisers with api.py
  ui/app.py          page navigation
  ui/ideas.py        landing page: shortlist as cards
  ui/dashboard.py    detailed screen: table, search, drill-down
  prices.py          merges the two exchanges: ISIN matching, NSE precedence
  sources/screener.py  company page parser
  sources/bhavcopy.py  the UDiFF rows both exchanges agree on
  sources/nse.py       NSE bhavcopy: zipped, 404s for a missing day
  sources/bse.py       BSE bhavcopy: bare CSV, 200s with HTML for one
  export/workbook.py   SSP workbook writer
tests/               287 tests; screener parsing runs off a saved fixture,
                     the dashboard off Streamlit's AppTest harness
```

`pytest` needs no network — the screener test uses `tests/fixtures/coastcorp.html`.
