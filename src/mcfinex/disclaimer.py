"""One wording for the disclaimer, used everywhere the signals surface.

Written once so the site, the exported CSV, the API and the README cannot drift
into saying different things about what these numbers are. A reader who meets a
BUY on the landing page, in a downloaded file, or through the API should meet
the same caveat.
"""

from __future__ import annotations

#: Shown beside the signals themselves, where a reader is about to act.
SHORT = (
    "Signals are generated mechanically from published financials and are "
    "subjective. Do your own research before acting on anything here."
)

#: The full statement, for page footers, the README and the API description.
FULL = """
**This is not investment advice.**

Every signal on this site is produced by a mechanical model applied to publicly
filed financial data. The thresholds that turn a number into BUY, HOLD or SELL
were chosen by hand — a different set of thresholds, applied to the same data,
would produce different verdicts. They are one opinion expressed as arithmetic,
not a finding.

The underlying figures are scraped from third-party sources and may be stale,
incomplete or wrong. Several measures are unavailable for some companies and
are withheld rather than estimated; where the model cannot support a view it
says UNKNOWN, and that is not a neutral verdict. Valuation targets extrapolate
past growth, which is not a forecast of future performance.

The operator is not a SEBI-registered investment adviser or research analyst
and provides no personalised advice. Nothing here accounts for your financial
position, objectives or risk tolerance.

**Do your own research, and consult a registered adviser before making any
investment decision.** You are solely responsible for what you do with this
information.
""".strip()

#: Prefixed to exported files, which travel away from the page that explained them.
CSV_HEADER = (
    "# MCFinEx screen. Not investment advice. Signals are generated "
    "mechanically from published financials and are subjective; the operator "
    "is not a registered investment adviser. Do your own research."
)
