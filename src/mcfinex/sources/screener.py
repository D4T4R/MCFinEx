"""Scrape a company page from screener.in.

Screener renders its financials as plain server-side HTML with labelled rows
and ``data-date-key`` attributes on the period headers, so no browser is
needed and nothing depends on a row's position in the table. That is the whole
reason this replaced the MoneyControl/Selenium path: MoneyControl was addressed
by absolute XPath (``tbody/tr[37]/td[2]``) and broke on every layout tweak.
"""

from __future__ import annotations

import calendar
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.screener.in"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Sections we read, keyed by the <section id> screener uses.
SECTIONS = ("quarters", "profit-loss", "balance-sheet", "cash-flow", "ratios", "shareholding")

_NUMERIC_JUNK = re.compile(r"[,%₹\s]")


class ScreenerError(RuntimeError):
    """Raised when a page cannot be fetched or is not a company page."""


class RateLimited(ScreenerError):
    """Screener returned 429 and the retries were exhausted."""


class Throttle:
    """A self-adjusting delay shared across a scrape run.

    Screener rate-limits, and a fixed delay cannot know where the ceiling is:
    a run at 0.7s was throttled from the 71st company onwards and kept hammering
    for 28 minutes, losing 716 of them. So the delay backs off hard on a 429 and
    recovers slowly on sustained success, settling near whatever the service
    will actually tolerate.
    """

    def __init__(self, delay: float = 1.0, *, maximum: float = 60.0):
        self.base = delay
        self.delay = delay
        self.maximum = maximum
        self._good_streak = 0

    def wait(self) -> None:
        if self.delay:
            time.sleep(self.delay)

    def penalise(self) -> float:
        """Double the delay after a rejection and report the new value."""
        self._good_streak = 0
        self.delay = min(max(self.delay * 2, 1.0), self.maximum)
        return self.delay

    def reward(self) -> None:
        """Ease back towards the base delay after a clean run of requests."""
        self._good_streak += 1
        if self._good_streak >= 20 and self.delay > self.base:
            self.delay = max(self.base, self.delay * 0.8)
            self._good_streak = 0


def to_number(text: str | None) -> float | None:
    """Parse a screener cell into a float, or ``None`` when there is no value.

    Screener writes blanks as ``""`` or ``"-"`` and decorates numbers with
    commas, percent signs and rupee symbols. Anything unparseable becomes
    ``None`` -- never a sentinel like the Java build's ``-9999999``, which used
    to land in numeric DB columns and silently poison every downstream average.
    """
    if text is None:
        return None
    cleaned = _NUMERIC_JUNK.sub("", text).strip()
    if cleaned in ("", "-", "--"):
        return None
    return float(cleaned) if _is_float(cleaned) else None


def _is_float(s: str) -> bool:
    try:
        float(s)
    except ValueError:
        return False
    return True


def _text(node) -> str:
    return node.get_text(" ", strip=True).replace("\xa0", " ") if node else ""


def _month_year(label: str) -> date | None:
    """Parse a ``"Sep 2023"`` column heading into that month's last day."""
    try:
        parsed = datetime.strptime(label.strip(), "%b %Y")
    except ValueError:
        return None
    last_day = calendar.monthrange(parsed.year, parsed.month)[1]
    return date(parsed.year, parsed.month, last_day)


@dataclass
class Section:
    """One financial table: period columns and label-keyed rows.

    ``periods`` holds one entry per data column, ``None`` where the column is
    not a real reporting period -- screener appends a trailing-twelve-months
    column keyed ``"TTM"``. Keeping it in place preserves row alignment;
    :meth:`series` drops it so growth math only sees comparable periods.
    """

    periods: list[date | None] = field(default_factory=list)
    rows: dict[str, list[float | None]] = field(default_factory=dict)

    def series(self, *labels: str) -> list[float | None]:
        """Row values for real reporting periods only, oldest first."""
        return [v for v, p in zip(self.row(*labels), self.periods) if p is not None]

    def dated_periods(self) -> list[date]:
        return [p for p in self.periods if p is not None]

    def row(self, *labels: str) -> list[float | None]:
        """Row values for the first label that matches, else all-``None``.

        Matching is case-insensitive and prefix-based so that screener's
        expandable rows (``"Borrowings +"``) match a plain ``"Borrowings"``.
        """
        for label in labels:
            want = label.casefold()
            for key, values in self.rows.items():
                normalised = key.rstrip("+ ").casefold()
                if normalised == want or normalised.startswith(want):
                    return values
        return [None] * len(self.periods)

    def latest(self, *labels: str) -> float | None:
        """Newest reported value, ignoring the TTM column.

        Reads through :meth:`series` so a derived figure can never mix a TTM
        number into an otherwise fiscal-year calculation.
        """
        values = [v for v in self.series(*labels) if v is not None]
        return values[-1] if values else None


@dataclass
class Company:
    ticker: str
    name: str | None = None
    #: Screener's internal id, which is the BSE scrip code. Needed by the
    #: schedules API; captured during the normal scrape so enrichment later
    #: costs no extra page fetch.
    company_id: int | None = None
    consolidated: bool = False
    industry: list[str] = field(default_factory=list)
    ratios: dict[str, float | None] = field(default_factory=dict)
    sections: dict[str, Section] = field(default_factory=dict)

    def section(self, name: str) -> Section:
        return self.sections.get(name, Section())

    @property
    def latest_period(self) -> date | None:
        """End date of the most recent quarter screener has results for."""
        periods = self.section("quarters").dated_periods()
        return periods[-1] if periods else None


def fetch(ticker: str, *, consolidated: bool = False, session: requests.Session | None = None,
          timeout: float = 20.0, delay: float = 1.0, throttle: Throttle | None = None,
          retries: int = 4) -> str:
    """Download a company page, backing off when screener pushes back.

    A 429 is a request to slow down, not a missing company, so it is retried
    with an exponential backoff that honours ``Retry-After`` when sent. Pass a
    shared :class:`Throttle` across a run so one rejection slows every
    subsequent request rather than only this one.
    """
    path = f"/company/{ticker.upper()}/" + ("consolidated/" if consolidated else "")
    sess = session or requests.Session()
    pace = throttle if throttle is not None else Throttle(delay)

    for attempt in range(retries + 1):
        pace.wait()
        resp = sess.get(BASE_URL + path, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        if resp.status_code == 404:
            raise ScreenerError(f"{ticker}: no such company on screener.in")
        if resp.status_code == 429:
            slowed = pace.penalise()
            if attempt == retries:
                raise RateLimited(
                    f"{ticker}: rate limited after {retries} retries (delay now {slowed:.1f}s)"
                )
            time.sleep(_retry_after(resp, fallback=slowed))
            continue
        resp.raise_for_status()
        pace.reward()
        return resp.text
    raise RateLimited(f"{ticker}: rate limited")  # unreachable, keeps type checkers happy


def _retry_after(resp: requests.Response, *, fallback: float) -> float:
    """Seconds to wait, preferring the server's own instruction."""
    header = resp.headers.get("Retry-After")
    if header:
        try:
            return min(float(header), 300.0)
        except ValueError:
            pass
    return fallback


def parse(html: str, ticker: str, *, consolidated: bool = False) -> Company:
    """Turn a company page into a :class:`Company`."""
    soup = BeautifulSoup(html, "lxml")
    company = Company(ticker=ticker.upper(), consolidated=consolidated)

    heading = soup.find("h1")
    if heading is None:
        raise ScreenerError(f"{ticker}: page has no <h1>, probably not a company page")
    company.name = _text(heading)
    company.company_id = _parse_company_id(soup)

    company.ratios = _parse_top_ratios(soup)
    company.industry = _parse_industry(soup)
    for name in SECTIONS:
        parsed = _parse_section(soup, name)
        if parsed:
            company.sections[name] = parsed
    return company


def _parse_company_id(soup) -> int | None:
    node = soup.find(attrs={"data-company-id": True})
    if node is None:
        return None
    try:
        return int(node["data-company-id"])
    except (TypeError, ValueError):
        return None


def fetch_schedule(company_id: int, parent: str, section: str = "balance-sheet", *,
                   session: requests.Session | None = None, timeout: float = 20.0,
                   throttle: Throttle | None = None) -> dict[str, dict[str, str]]:
    """Fetch the breakdown behind an expandable row.

    Screener's balance sheet collapses detail into ``Other Assets``,
    ``Other Liabilities`` and ``Borrowings``; the page expands them through
    ``/api/company/{id}/schedules/``, which returns plain JSON. This is what
    supplies cash, the current asset and liability components, and the
    long/short borrowings split -- none of which appear on the page itself.

    Returns ``{line item: {period: value}}``, empty when there is no schedule.
    """
    sess = session or requests.Session()
    if throttle is not None:
        throttle.wait()
    resp = sess.get(
        f"{BASE_URL}/api/company/{company_id}/schedules/",
        params={"parent": parent, "section": section},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=timeout,
    )
    if resp.status_code == 429:
        raise RateLimited(f"schedule {parent}: rate limited")
    if resp.status_code == 404:
        return {}
    resp.raise_for_status()
    try:
        payload = resp.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_top_ratios(soup) -> dict[str, float | None]:
    """The headline box: Market Cap, Current Price, Stock P/E, Book Value..."""
    ratios: dict[str, float | None] = {}
    container = soup.find(id="top-ratios")
    if not container:
        return ratios
    for item in container.find_all("li"):
        label = _text(item.find("span", class_="name"))
        if not label:
            continue
        numbers = [to_number(_text(n)) for n in item.find_all("span", class_="number")]
        # "High / Low" carries two numbers; split it rather than dropping one.
        if label.lower().startswith("high") and len(numbers) >= 2:
            ratios["High"], ratios["Low"] = numbers[0], numbers[1]
        else:
            ratios[label] = numbers[0] if numbers else None
    return ratios


def _parse_industry(soup) -> list[str]:
    """Industry breadcrumb, broadest first.

    Screener links each level to ``/market/<codes>/`` and tags it with a
    ``title`` of Sector, Broad Industry or Industry, e.g.
    ``["Fast Moving Consumer Goods", "Food Products", "Seafood"]``.
    """
    levels = ("Sector", "Broad Industry", "Industry")
    found: dict[str, str] = {}
    for link in soup.select('a[href^="/market/"]'):
        title = link.get("title")
        label = _text(link)
        if title in levels and label:
            found.setdefault(title, label)
    return [found[level] for level in levels if level in found]


def _parse_section(soup, section_id: str) -> Section | None:
    node = soup.find("section", id=section_id)
    if node is None:
        return None
    table = node.find("table")
    if table is None:
        return None

    section = Section()
    head = table.find("thead")
    if head:
        for th in head.find_all("th"):
            # The shareholding table has no data-date-key, only "Sep 2023"
            # headings, so fall back to the visible text.
            key = th.get("data-date-key")
            if key is None:
                label = _text(th)
                if not label:
                    continue  # the blank corner cell above the row labels
                section.periods.append(_month_year(label))
                continue
            try:
                section.periods.append(datetime.strptime(key, "%Y-%m-%d").date())
            except ValueError:
                # "TTM" and friends: a real column, but not a reporting period.
                section.periods.append(None)

    body = table.find("tbody")
    width = len(section.periods)
    for tr in (body.find_all("tr") if body else []):
        cells = tr.find_all("td")
        if not cells:
            continue
        label = _text(cells[0])
        if not label:
            continue
        values = [to_number(_text(c)) for c in cells[1:]]
        # Pad or trim so every row lines up with the period columns.
        values = (values + [None] * width)[:width]
        # Screener repeats row labels across sub-tables (shareholding shows
        # quarterly then yearly); keep the first, which is the one on screen.
        section.rows.setdefault(label, values)
    return section
