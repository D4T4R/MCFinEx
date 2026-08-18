"""Screener line-item labels, and the aliases that mean the same thing.

Screener renders banks, NBFCs and housing-finance companies with a different
vocabulary from ordinary companies: ``Revenue`` for ``Sales``, ``Financing
Profit`` for ``Operating Profit``, singular ``Borrowing``, plus a ``Deposits``
row that non-financials do not have.

Looking a label up by one exact spelling therefore finds nothing for ~8% of
listed companies, and a cell that is never written keeps whatever stale value
it already held. Every lookup goes through these tuples, most-common spelling
first.
"""

from __future__ import annotations

# Balance sheet
EQUITY_CAPITAL = ("Equity Capital",)
RESERVES = ("Reserves",)
BORROWINGS = ("Borrowings", "Borrowing")
OTHER_LIABILITIES = ("Other Liabilities",)
DEPOSITS = ("Deposits",)
TOTAL_LIABILITIES = ("Total Liabilities",)
INVESTMENTS = ("Investments",)

# Profit and loss
SALES = ("Sales", "Revenue")
# "Financing Profit" is the closest analogue banks report. EV/EBITDA is not a
# meaningful way to value a bank, but the workbook has the columns, so the
# nearest equivalent is supplied rather than leaving stale numbers behind.
OPERATING_PROFIT = ("Operating Profit", "Financing Profit")
PROFIT_BEFORE_TAX = ("Profit before tax",)
INTEREST = ("Interest",)
EPS = ("EPS in Rs",)

# Cash flow
CASH_FROM_OPERATING = ("Cash from Operating Activity",)
CASH_FROM_INVESTING = ("Cash from Investing Activity",)
CASH_FROM_FINANCING = ("Cash from Financing Activity",)
FREE_CASH_FLOW = ("Free Cash Flow",)

# Ratios and shareholding
INVENTORY_DAYS = ("Inventory Days",)
PROMOTERS = ("Promoters",)
