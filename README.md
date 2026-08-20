# MCFinEx

Scrapes Indian company financials from [screener.in](https://www.screener.in) into
SQLite, then fills the input cells of the SSP valuation workbook.

A Python rewrite of the Java/Selenium MCFinEx, which scraped moneycontrol.com by
absolute XPath. Those XPaths no longer resolve, and neither does the NSE URL the
old bhavcopy loader used.

> Educational use only. Nothing here is investment advice, and no financial
> decision should rest on numbers this tool produced.

## How it works

```
NSE bhavcopy ──> tickers + ISINs ──┐
                                   ├──> SQLite ──> SSP workbook (Excel computes)
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
mcfinex scrape RELIANCE TCS           # scrape named companies
mcfinex scrape --from-template        # scrape the companies tracked in the workbook
mcfinex scrape --all --limit 50       # or work through the seeded universe
mcfinex prices                        # refresh closing prices from the NSE bhavcopy
mcfinex enrich RELIANCE TCS           # pull balance-sheet detail for named companies
mcfinex show RELIANCE                 # print stored values and valuations
mcfinex screen --min-buys 6           # rank by BUY signals
mcfinex screen --csv screen.csv       # or dump the whole screen
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
current P/E and every target price, so `prices` overwrites it with the exact NSE
close and recomputes the stored valuations. It is one download rather than one
request per company, so it is quick enough to run daily: prices move daily,
fundamentals quarterly.

Settings are environment variables, all optional:

| Variable | Default |
|---|---|
| `MCFINEX_DB` | `data/stocks.db` |
| `MCFINEX_TEMPLATE` | `~/Downloads/SSP_Working_merged.xlsx` |
| `MCFINEX_EXPORT` | `data/SSP_Working_populated.xlsx` |
| `MCFINEX_REQUEST_DELAY` | `1.0` seconds between screener requests (see below) |

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
  api.py             read-only FastAPI over the database
  ui/app.py          page navigation
  ui/ideas.py        landing page: shortlist as cards
  ui/dashboard.py    detailed screen: table, search, drill-down
  sources/screener.py  company page parser
  sources/nse.py       bhavcopy loader
  export/workbook.py   SSP workbook writer
tests/               287 tests; screener parsing runs off a saved fixture,
                     the dashboard off Streamlit's AppTest harness
```

`pytest` needs no network — the screener test uses `tests/fixtures/coastcorp.html`.
