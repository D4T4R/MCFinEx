"""Fundamental and valuation signals.

This replaces the workbook's STRATEGY columns (F, K, Q, U, Z, AC, AI, AN, AR,
AT, CB, CC, CT, DL) and its Results sheet. Those were `IF` chains pointing at a
lookup table that has since been deleted, so every one of them evaluated to
`#REF!`; the only surviving evidence of their vocabulary is
`Results!M = COUNTIF(C:L,"BUY")`. The thresholds below are recovered from the
surviving `IF` conditions, and are stated once, here, in Python.

Pure by design: no database, no UI, no I/O. Everything takes plain numbers and
returns plain values, so the same logic serves the CLI, the Streamlit app and
anything added later.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from enum import Enum

#: Investopedia blocks automated requests, so deep links such as
#: /terms/p/price-to-bookratio.asp cannot be verified from here. A search URL
#: always resolves and costs one extra click, which beats a broken deep link.
_REFERENCE = "https://www.investopedia.com/search?q="


def reference(term: str) -> str:
    return _REFERENCE + urllib.parse.quote_plus(term)


@dataclass(frozen=True)
class Explanation:
    """Why a signal reads the way it does, in numbers a reader can re-check.

    Definitions are written here rather than quoted, with a link out for
    further reading.
    """

    definition: str
    formula: str
    #: Named inputs in the order they appear in the formula.
    inputs: tuple[tuple[str, float | None], ...]
    #: The threshold sentence, e.g. "8.55 is above 3, so SELL".
    reasoning: str
    term: str

    @property
    def url(self) -> str:
        return reference(self.term)

    def arithmetic(self, result: float | None) -> str:
        """The calculation written out, e.g. "670.05 / 78.40 = 8.55"."""
        known = [v for _, v in self.inputs if v is not None]
        if len(known) < 2 or result is None:
            return ""
        symbol = " / " if "/" in self.formula or "\u00f7" in self.formula else " + "
        return f"{symbol.join(f'{v:,.2f}' for v in known)} = {result:,.2f}"


class Verdict(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    #: The input is unavailable, so no opinion is offered. Distinct from HOLD,
    #: which is a genuine neutral reading, and excluded from the score.
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Signal:
    key: str
    label: str
    verdict: Verdict
    value: float | None
    rule: str
    #: False for signals screener cannot supply, so the UI can grey them out
    #: rather than implying the company failed the test.
    available: bool = True
    #: The numbers and definition behind the verdict, for the drill-down.
    explanation: Explanation | None = None


@dataclass
class Metrics:
    """Everything a screen needs, already derived. All in rupees crore."""

    ticker: str
    name: str | None = None
    sector: str | None = None
    price: float | None = None
    # Balance sheet
    reserves: float | None = None
    equity_capital: float | None = None
    other_liabilities: float | None = None
    borrowings: float | None = None
    # Performance
    roce: float | None = None
    inventory_days: float | None = None
    inventory_days_prior: float | None = None
    free_cash_flow: float | None = None
    # Market
    stock_pe: float | None = None
    sector_pe: float | None = None
    book_value: float | None = None
    dividend_yield: float | None = None
    promoter_holding: float | None = None
    promoter_pledge: float | None = None
    # Only present once the balance-sheet schedules have been fetched.
    current_assets: float | None = None
    current_liabilities: float | None = None
    #: Quarterly results reported since listing. Zero means the company has not
    #: yet reported as a listed entity, so its per-share history spans the IPO
    #: and is not comparable across periods. ``None`` means simply not counted,
    #: which must not be mistaken for "no history" -- only a measured zero
    #: withholds a signal.
    quarters_reported: int | None = None
    #: Banks, NBFCs and insurers. EV/EBITDA is not a meaningful way to value
    #: them -- borrowings are raw material, not capital structure -- so the
    #: EV-based signals are withheld rather than reported as a number.
    is_financial: bool = False
    # Valuation model outputs, as percentages
    ev_ebitda_upside: float | None = None
    ev_ebitda_upside_with_debt: float | None = None
    pe_yearly_rerating: float | None = None
    pe_quarterly_rerating: float | None = None

    @property
    def newly_listed(self) -> bool:
        """No quarterly results yet, so per-share history straddles the IPO."""
        return self.quarters_reported == 0


@dataclass
class Screening:
    ticker: str
    name: str | None
    sector: str | None
    fundamentals: list[Signal] = field(default_factory=list)
    valuations: list[Signal] = field(default_factory=list)

    @property
    def signals(self) -> list[Signal]:
        return self.fundamentals + self.valuations

    @property
    def buy_count(self) -> int:
        """The workbook's OVERALL: how many fundamental screens say BUY."""
        return sum(1 for s in self.fundamentals if s.verdict is Verdict.BUY)

    @property
    def scored_count(self) -> int:
        """Fundamental signals that had data, i.e. the denominator of the score."""
        return sum(1 for s in self.fundamentals if s.verdict is not Verdict.UNKNOWN)

    @property
    def sell_count(self) -> int:
        return sum(1 for s in self.fundamentals if s.verdict is Verdict.SELL)

    def get(self, key: str) -> Signal | None:
        return next((s for s in self.signals if s.key == key), None)


def _band(value: float | None, *, buy: float, sell: float,
          lower_is_better: bool = False) -> Verdict:
    """Classify a value against a two-sided band, anything between is HOLD.

    ``buy`` and ``sell`` are the thresholds the verdict is named after, so for
    debt-to-equity (``lower_is_better``) ``buy=1, sell=2`` means below 1 is a
    BUY and above 2 is a SELL.
    """
    if value is None:
        return Verdict.UNKNOWN
    if lower_is_better:
        if value < buy:
            return Verdict.BUY
        if value > sell:
            return Verdict.SELL
        return Verdict.HOLD
    if value > buy:
        return Verdict.BUY
    if value < sell:
        return Verdict.SELL
    return Verdict.HOLD


def _verdict_sentence(value: float | None, verdict: Verdict, *, buy: float, sell: float,
                      unit: str = "", lower_is_better: bool = False) -> str:
    """One sentence tying the measured value to the threshold it crossed."""
    if value is None:
        return "No value could be computed, so no verdict is offered."
    shown = f"{value:,.2f}{unit}"
    crossed = buy if verdict is Verdict.BUY else sell
    if verdict is Verdict.HOLD:
        low, high = (buy, sell) if lower_is_better else (sell, buy)
        return f"{shown} sits between {low:,.2f}{unit} and {high:,.2f}{unit}, so HOLD."
    side = "below" if (verdict is Verdict.BUY) == lower_is_better else "above"
    return f"{shown} is {side} {crossed:,.2f}{unit}, so {verdict.value}."


def screen(m: Metrics) -> Screening:
    """Score one company."""
    return Screening(
        ticker=m.ticker,
        name=m.name,
        sector=m.sector,
        fundamentals=[
            _promoter(m), _reserves_to_capital(m), _debt_to_equity(m),
            _current_ratio(m), _roce(m), _inventory(m), _free_cash_flow(m),
            _pe(m), _price_to_book(m), _dividend_yield(m),
        ],
        valuations=[
            _ev_ebitda(m), _ev_ebitda_with_debt(m),
            _pe_yearly(m), _pe_quarterly(m),
        ],
    )


# --------------------------------------------------------------- fundamentals

def _promoter(m: Metrics) -> Signal:
    # The workbook screened promoter *pledge* (>10% was the fail). Screener does
    # not publish pledge, so promoter holding stands in: a high, stable
    # promoter stake is the same underlying "is the owner committed" question.
    if m.promoter_pledge is not None:
        verdict = Verdict.SELL if m.promoter_pledge > 10 else Verdict.BUY
        return Signal("promoter", "Promoter pledge", verdict, m.promoter_pledge,
                      "pledge >10% is a fail")
    verdict = _band(m.promoter_holding, buy=50, sell=25)
    return Signal(
        "promoter", "Promoter holding", verdict, m.promoter_holding,
        "holding >50% BUY, <25% SELL (pledge unavailable)",
        explanation=Explanation(
            definition=(
                "Promoters are the founding or controlling owners of an Indian "
                "listed company. A large stake means the people running the "
                "business carry the same downside as outside shareholders; a "
                "shrinking or small one removes that alignment."
            ),
            formula="promoter shareholding as a % of equity",
            inputs=(("Promoter holding %", m.promoter_holding),),
            reasoning=_verdict_sentence(m.promoter_holding, verdict, buy=50, sell=25, unit="%"),
            term="promoter shareholding",
        ),
    )


def _reserves_to_capital(m: Metrics) -> Signal:
    ratio = None
    if m.reserves is not None and m.equity_capital:
        ratio = m.reserves / m.equity_capital
    verdict = _band(ratio, buy=5, sell=0)
    return Signal(
        "reserves_to_capital", "Reserves / capital", verdict, ratio, ">5 BUY, <0 SELL",
        explanation=Explanation(
            definition=(
                "Reserves are profits the company kept rather than paid out, "
                "accumulated over its life. Measured against the equity capital "
                "originally subscribed, this shows how much the business has "
                "compounded on its own account. A negative figure means "
                "accumulated losses have eaten through the original capital."
            ),
            formula="reserves / equity capital",
            inputs=(("Reserves", m.reserves), ("Equity capital", m.equity_capital)),
            reasoning=_verdict_sentence(ratio, verdict, buy=5, sell=0),
            term="retained earnings",
        ),
    )


def _debt_to_equity(m: Metrics) -> Signal:
    ratio, equity = None, None
    if m.reserves is not None and m.equity_capital is not None:
        equity = m.reserves + m.equity_capital
    if equity:
        liabilities = (m.other_liabilities or 0.0) + (m.borrowings or 0.0)
        ratio = liabilities / equity
    verdict = _band(ratio, buy=1, sell=2, lower_is_better=True)
    return Signal(
        "debt_to_equity", "Debt / equity", verdict, ratio, "<1 BUY, >2 SELL",
        explanation=Explanation(
            definition=(
                "What the company owes set against what the owners have in it. "
                "Debt magnifies returns in good years and losses in bad ones, so "
                "a high ratio means earnings are more sensitive to a downturn "
                "and interest must be paid whatever happens."
            ),
            formula="(other liabilities + borrowings) / (reserves + equity capital)",
            inputs=(
                ("Other liabilities", m.other_liabilities),
                ("Borrowings", m.borrowings),
                ("Reserves", m.reserves),
                ("Equity capital", m.equity_capital),
            ),
            reasoning=_verdict_sentence(ratio, verdict, buy=1, sell=2, lower_is_better=True),
            term="debt to equity ratio",
        ),
    )


def _current_ratio(m: Metrics) -> Signal:
    # The company page gives no current/non-current split, but the balance-sheet
    # schedules do. Until `mcfinex enrich` has fetched them this stays UNKNOWN
    # rather than guessed -- a fabricated current ratio is worse than none.
    if m.current_assets is None or not m.current_liabilities:
        return Signal("current_ratio", "Current ratio", Verdict.UNKNOWN, None,
                      "run enrich to fetch the balance-sheet detail", available=False)
    ratio = m.current_assets / m.current_liabilities
    verdict = _band(ratio, buy=1.5, sell=1.0)
    return Signal(
        "current_ratio", "Current ratio", verdict, ratio, ">1.5 BUY, <1 SELL",
        explanation=Explanation(
            definition=(
                "Assets expected to turn into cash within a year, against the "
                "bills due in the same period. Below 1 the company owes more in "
                "the short term than it expects to realise, which means relying "
                "on refinancing rather than on its own working capital."
            ),
            formula="current assets / current liabilities",
            inputs=(("Current assets", m.current_assets),
                    ("Current liabilities", m.current_liabilities)),
            reasoning=_verdict_sentence(ratio, verdict, buy=1.5, sell=1.0),
            term="current ratio",
        ),
    )


def _roce(m: Metrics) -> Signal:
    # Taken from screener's own ROCE rather than EBIT / capital employed, which
    # needs the current-liability figure we do not have.
    verdict = _band(m.roce, buy=10, sell=10)
    return Signal(
        "roce", "ROCE %", verdict, m.roce, ">10% BUY, <10% SELL",
        explanation=Explanation(
            definition=(
                "Operating profit as a percentage of all the capital the "
                "business employs, debt and equity together. It answers what the "
                "company earns on every rupee put to work, regardless of how "
                "that money was raised. Below the cost of borrowing, growth "
                "destroys value rather than creating it."
            ),
            formula="reported by screener as return on capital employed",
            inputs=(("ROCE %", m.roce),),
            reasoning=_verdict_sentence(m.roce, verdict, buy=10, sell=10, unit="%"),
            term="return on capital employed",
        ),
    )


def _inventory(m: Metrics) -> Signal:
    # Screener reports Inventory Days; turnover is 365/days, so fewer days is
    # better. Judged on the trend, since the right absolute level is entirely
    # sector-dependent.
    if m.inventory_days is None:
        return Signal("inventory", "Inventory turnover", Verdict.UNKNOWN, None,
                      "no inventory (financials and most services)", available=False)
    turnover = 365.0 / m.inventory_days if m.inventory_days else None
    if m.inventory_days_prior is None or not m.inventory_days_prior:
        return Signal("inventory", "Inventory turnover", Verdict.HOLD, turnover,
                      "no prior year to compare")
    change = (m.inventory_days - m.inventory_days_prior) / m.inventory_days_prior * 100
    verdict = Verdict.BUY if change < -5 else Verdict.SELL if change > 5 else Verdict.HOLD
    moved = "fell" if change < 0 else "rose"
    return Signal(
        "inventory", "Inventory turnover", verdict, turnover,
        "days falling >5% BUY, rising >5% SELL",
        explanation=Explanation(
            definition=(
                "How many times a year the company sells and replaces its "
                "stock, derived from how many days of inventory it holds. Fewer "
                "days means goods are moving and less cash is tied up on "
                "shelves. Rising days can be an early sign that demand is "
                "softening before it shows up in sales. The right absolute level "
                "is entirely sector-dependent, so the trend is judged, not the "
                "level."
            ),
            formula="365 / inventory days, compared with the prior year",
            inputs=(("Inventory days (latest)", m.inventory_days),
                    ("Inventory days (prior)", m.inventory_days_prior)),
            reasoning=(
                f"Inventory days {moved} {abs(change):,.1f}% from "
                f"{m.inventory_days_prior:,.0f} to {m.inventory_days:,.0f}, so {verdict.value}."
            ),
            term="inventory turnover",
        ),
    )


def _free_cash_flow(m: Metrics) -> Signal:
    if m.free_cash_flow is None:
        return Signal("free_cash_flow", "Free cash flow", Verdict.UNKNOWN, None,
                      "not reported", available=False)
    verdict = Verdict.BUY if m.free_cash_flow > 0 else Verdict.SELL
    return Signal(
        "free_cash_flow", "Free cash flow", verdict, m.free_cash_flow, ">0 BUY, <0 SELL",
        explanation=Explanation(
            definition=(
                "Cash generated by operations after the capital spending needed "
                "to keep the business running. It is what is genuinely left over "
                "for dividends, debt repayment or reinvestment. Profit is an "
                "accounting opinion; this is closer to fact."
            ),
            formula="operating cash flow - capital expenditure (as reported)",
            inputs=(("Free cash flow (Rs crore)", m.free_cash_flow),),
            reasoning=(
                f"{m.free_cash_flow:,.2f} crore is "
                f"{'positive, so BUY' if m.free_cash_flow > 0 else 'negative, so SELL'}."
            ),
            term="free cash flow",
        ),
    )


def _pe(m: Metrics) -> Signal:
    # The workbook compared stock P/E with an industry P/E from MoneyControl.
    # Screener does not expose one, so the sector median across everything
    # scraped is used instead -- computed from our own data, no extra requests.
    if m.stock_pe is None or m.sector_pe is None:
        return Signal("pe", "P/E vs sector", Verdict.UNKNOWN, m.stock_pe,
                      "no sector median available", available=False)
    if m.stock_pe <= 0:
        return Signal("pe", "P/E vs sector", Verdict.SELL, m.stock_pe,
                      "loss-making")
    premium = (m.stock_pe - m.sector_pe) / m.sector_pe * 100
    verdict = Verdict.BUY if premium < -10 else Verdict.SELL if premium > 10 else Verdict.HOLD
    direction = "below" if premium < 0 else "above"
    return Signal(
        "pe", "P/E vs sector", verdict, m.stock_pe,
        ">10% below sector median BUY, >10% above SELL",
        explanation=Explanation(
            definition=(
                "Price paid per rupee of annual earnings. On its own the number "
                "means little, since a steel mill and a software firm are "
                "priced differently by nature, so it is judged against the "
                "median of the same industry. Cheaper than peers can mean "
                "overlooked, or it can mean the market expects earnings to fall."
            ),
            formula="stock P/E vs the median P/E of its industry",
            inputs=(("Stock P/E", m.stock_pe), ("Sector median P/E", m.sector_pe)),
            reasoning=(
                f"{m.stock_pe:,.2f} is {abs(premium):,.1f}% {direction} the sector "
                f"median of {m.sector_pe:,.2f}, so {verdict.value}."
            ),
            term="price earnings ratio",
        ),
    )


def _price_to_book(m: Metrics) -> Signal:
    ratio = m.price / m.book_value if m.price and m.book_value else None
    verdict = _band(ratio, buy=1.5, sell=3, lower_is_better=True)
    return Signal(
        "price_to_book", "Price / book", verdict, ratio, "<1.5 BUY, >3 SELL",
        explanation=Explanation(
            definition=(
                "Share price against the accounting value of the assets behind "
                "each share. Below 1 the market is paying less than the balance "
                "sheet claims the company is worth. A high multiple is not "
                "automatically bad -- asset-light businesses earn from people "
                "and brands that the balance sheet barely records -- but it does "
                "mean little asset backing under the price."
            ),
            formula="share price / book value per share",
            inputs=(("Share price", m.price), ("Book value per share", m.book_value)),
            reasoning=_verdict_sentence(ratio, verdict, buy=1.5, sell=3, lower_is_better=True),
            term="price to book ratio",
        ),
    )


def _dividend_yield(m: Metrics) -> Signal:
    # The workbook used a 5% cutoff, which almost nothing on the NSE clears.
    # 1.5% is a realistic "pays a meaningful dividend" bar.
    if m.dividend_yield is None:
        return Signal("dividend_yield", "Dividend yield %", Verdict.UNKNOWN, None,
                      "not reported", available=False)
    verdict = (Verdict.BUY if m.dividend_yield >= 1.5
               else Verdict.HOLD if m.dividend_yield > 0 else Verdict.SELL)
    if m.dividend_yield >= 1.5:
        why = f"{m.dividend_yield:,.2f}% is at or above 1.50%, so BUY."
    elif m.dividend_yield > 0:
        why = f"{m.dividend_yield:,.2f}% is positive but under 1.50%, so HOLD."
    else:
        why = "The company pays no dividend, so SELL."
    return Signal(
        "dividend_yield", "Dividend yield %", verdict, m.dividend_yield,
        ">=1.5% BUY, 0 SELL",
        explanation=Explanation(
            definition=(
                "Annual dividend as a percentage of the share price -- the cash "
                "return for simply holding. A paid dividend is hard evidence "
                "that reported profits are real. The absence of one is not "
                "always a fault: a company reinvesting at a high return may "
                "serve shareholders better by keeping the money."
            ),
            formula="dividend per share / share price",
            inputs=(("Dividend yield %", m.dividend_yield),),
            reasoning=why,
            term="dividend yield",
        ),
    )


# ---------------------------------------------------------------- valuations

def _upside(key: str, label: str, upside: float | None, rule: str,
            *, net_debt: bool = False) -> Signal:
    verdict = _band(upside, buy=10, sell=0)
    debt_note = (
        " This variant subtracts borrowings first, so it values what would be "
        "left for shareholders after the lenders are paid."
    ) if net_debt else ""
    return Signal(
        key, label, verdict, upside, rule,
        explanation=Explanation(
            definition=(
                "Enterprise value is what it would cost to buy the whole "
                "business: its market value plus debt. Set against EBITDA -- "
                "earnings before interest, tax, depreciation and amortisation -- "
                "it compares companies on operating performance alone, ignoring "
                "how they are financed. Here the recent EBITDA growth rate is "
                "projected forward, the current multiple re-applied, and the "
                "result turned into a per-share target. The upside is the gap "
                "between that target and today's price." + debt_note
            ),
            formula="(target price - current price) / current price",
            inputs=(("Upside %", upside),),
            reasoning=_verdict_sentence(upside, verdict, buy=10, sell=0, unit="%"),
            term="EV to EBITDA",
        ),
    )


def _ev_ebitda(m: Metrics) -> Signal:
    if m.is_financial:
        return Signal("ev_ebitda", "EV/EBITDA upside %", Verdict.UNKNOWN, None,
                      "not meaningful for banks and NBFCs", available=False)
    return _upside("ev_ebitda", "EV/EBITDA upside %", m.ev_ebitda_upside,
                   ">10% BUY, <0% SELL")


def _ev_ebitda_with_debt(m: Metrics) -> Signal:
    if m.is_financial:
        return Signal("ev_ebitda_net_debt", "EV/EBITDA upside % (net debt)",
                      Verdict.UNKNOWN, None,
                      "not meaningful for banks and NBFCs", available=False)
    return _upside("ev_ebitda_net_debt", "EV/EBITDA upside % (net debt)",
                   m.ev_ebitda_upside_with_debt, ">10% BUY, <0% SELL", net_debt=True)


def _rerating(key: str, label: str, pct: float | None, *, newly_listed: bool = False) -> Signal:
    # The workbook's CT and DL used a -5/+5 band on the P/E re-rating.
    if newly_listed:
        # An IPO multiplies the share count, so pre-listing EPS is not
        # comparable with post-listing EPS. Milky Mist's series reads
        # 90.71 -> 0.69, which the model would otherwise call a collapse.
        return Signal(key, label, Verdict.UNKNOWN, None,
                      "newly listed: per-share history spans the IPO", available=False)
    if pct is None:
        return Signal(key, label, Verdict.UNKNOWN, None, "not computable", available=False)
    verdict = Verdict.BUY if pct > 5 else Verdict.SELL if pct < -5 else Verdict.HOLD
    cadence = "four years" if "yearly" in key else "four quarters"
    return Signal(
        key, label, verdict, pct, ">5% BUY, <-5% SELL",
        explanation=Explanation(
            definition=(
                "Forward P/E prices the shares against expected earnings rather "
                f"than past ones. Earnings growth over the last {cadence} is "
                "projected one period forward; if the company earns more, the "
                "same share price represents a lower multiple. This is the gap "
                "between the current and forward P/E -- how far the rating would "
                "fall on its own if those earnings arrive. A negative figure "
                "means earnings are shrinking, so the rating gets more expensive "
                "while standing still."
            ),
            formula="(current P/E - forward P/E) / current P/E",
            inputs=(("Re-rating %", pct),),
            reasoning=_verdict_sentence(pct, verdict, buy=5, sell=-5, unit="%"),
            term="forward price to earnings",
        ),
    )


def _pe_yearly(m: Metrics) -> Signal:
    return _rerating("pe_yearly", "P/E re-rating % (yearly)", m.pe_yearly_rerating,
                     newly_listed=m.newly_listed)


def _pe_quarterly(m: Metrics) -> Signal:
    return _rerating("pe_quarterly", "P/E re-rating % (quarterly)", m.pe_quarterly_rerating,
                     newly_listed=m.newly_listed)
