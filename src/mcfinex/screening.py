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

from dataclasses import dataclass, field
from enum import Enum


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
    #: Banks, NBFCs and insurers. EV/EBITDA is not a meaningful way to value
    #: them -- borrowings are raw material, not capital structure -- so the
    #: EV-based signals are withheld rather than reported as a number.
    is_financial: bool = False
    # Valuation model outputs, as percentages
    ev_ebitda_upside: float | None = None
    ev_ebitda_upside_with_debt: float | None = None
    pe_yearly_rerating: float | None = None
    pe_quarterly_rerating: float | None = None


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
    return Signal("promoter", "Promoter holding", verdict, m.promoter_holding,
                  "holding >50% BUY, <25% SELL (pledge unavailable)")


def _reserves_to_capital(m: Metrics) -> Signal:
    ratio = None
    if m.reserves is not None and m.equity_capital:
        ratio = m.reserves / m.equity_capital
    verdict = _band(ratio, buy=5, sell=0)
    return Signal("reserves_to_capital", "Reserves / capital", verdict, ratio,
                  ">5 BUY, <0 SELL")


def _debt_to_equity(m: Metrics) -> Signal:
    ratio, equity = None, None
    if m.reserves is not None and m.equity_capital is not None:
        equity = m.reserves + m.equity_capital
    if equity:
        liabilities = (m.other_liabilities or 0.0) + (m.borrowings or 0.0)
        ratio = liabilities / equity
    verdict = _band(ratio, buy=1, sell=2, lower_is_better=True)
    return Signal("debt_to_equity", "Debt / equity", verdict, ratio,
                  "<1 BUY, >2 SELL")


def _current_ratio(m: Metrics) -> Signal:
    # The company page gives no current/non-current split, but the balance-sheet
    # schedules do. Until `mcfinex enrich` has fetched them this stays UNKNOWN
    # rather than guessed -- a fabricated current ratio is worse than none.
    if m.current_assets is None or not m.current_liabilities:
        return Signal("current_ratio", "Current ratio", Verdict.UNKNOWN, None,
                      "run enrich to fetch the balance-sheet detail", available=False)
    ratio = m.current_assets / m.current_liabilities
    return Signal("current_ratio", "Current ratio",
                  _band(ratio, buy=1.5, sell=1.0), ratio,
                  ">1.5 BUY, <1 SELL")


def _roce(m: Metrics) -> Signal:
    # Taken from screener's own ROCE rather than EBIT / capital employed, which
    # needs the current-liability figure we do not have.
    verdict = _band(m.roce, buy=10, sell=10)
    return Signal("roce", "ROCE %", verdict, m.roce, ">10% BUY, <10% SELL")


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
    return Signal("inventory", "Inventory turnover", verdict, turnover,
                  "days falling >5% BUY, rising >5% SELL")


def _free_cash_flow(m: Metrics) -> Signal:
    if m.free_cash_flow is None:
        return Signal("free_cash_flow", "Free cash flow", Verdict.UNKNOWN, None,
                      "not reported", available=False)
    verdict = Verdict.BUY if m.free_cash_flow > 0 else Verdict.SELL
    return Signal("free_cash_flow", "Free cash flow", verdict, m.free_cash_flow,
                  ">0 BUY, <0 SELL")


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
    return Signal("pe", "P/E vs sector", verdict, m.stock_pe,
                  ">10% below sector median BUY, >10% above SELL")


def _price_to_book(m: Metrics) -> Signal:
    ratio = m.price / m.book_value if m.price and m.book_value else None
    verdict = _band(ratio, buy=1.5, sell=3, lower_is_better=True)
    return Signal("price_to_book", "Price / book", verdict, ratio,
                  "<1.5 BUY, >3 SELL")


def _dividend_yield(m: Metrics) -> Signal:
    # The workbook used a 5% cutoff, which almost nothing on the NSE clears.
    # 1.5% is a realistic "pays a meaningful dividend" bar.
    if m.dividend_yield is None:
        return Signal("dividend_yield", "Dividend yield %", Verdict.UNKNOWN, None,
                      "not reported", available=False)
    verdict = (Verdict.BUY if m.dividend_yield >= 1.5
               else Verdict.HOLD if m.dividend_yield > 0 else Verdict.SELL)
    return Signal("dividend_yield", "Dividend yield %", verdict, m.dividend_yield,
                  ">=1.5% BUY, 0 SELL")


# ---------------------------------------------------------------- valuations

def _upside(key: str, label: str, upside: float | None, rule: str) -> Signal:
    verdict = _band(upside, buy=10, sell=0)
    return Signal(key, label, verdict, upside, rule)


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
                   m.ev_ebitda_upside_with_debt, ">10% BUY, <0% SELL")


def _rerating(key: str, label: str, pct: float | None) -> Signal:
    # The workbook's CT and DL used a -5/+5 band on the P/E re-rating.
    if pct is None:
        return Signal(key, label, Verdict.UNKNOWN, None, "not computable", available=False)
    verdict = Verdict.BUY if pct > 5 else Verdict.SELL if pct < -5 else Verdict.HOLD
    return Signal(key, label, verdict, pct, ">5% BUY, <-5% SELL")


def _pe_yearly(m: Metrics) -> Signal:
    return _rerating("pe_yearly", "P/E re-rating % (yearly)", m.pe_yearly_rerating)


def _pe_quarterly(m: Metrics) -> Signal:
    return _rerating("pe_quarterly", "P/E re-rating % (quarterly)", m.pe_quarterly_rerating)
