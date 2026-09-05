"""Shared presentation helpers for the Streamlit pages."""

from __future__ import annotations

from datetime import date

#: Shown when the database is missing or empty. The pages used to print the CLI
#: command that would fix it, which reads as an instruction to whoever is
#: looking -- and on the deployed site that is a member of the public with no
#: repository, no virtualenv and no credentials. The hint is kept for local
#: development, but framed as a condition rather than a request.
NO_DATA = (
    "No data is available, which usually means the app is misconfigured rather "
    "than that nothing qualified. If you are running this locally, create the "
    "database with `mcfinex init` and populate it with `mcfinex scrape`."
)


def _human(stamp: str | None) -> str | None:
    """``2026-09-04`` as ``4 Sep 2026``; ``None`` if it is not a date."""
    if not stamp:
        return None
    try:
        parsed = date.fromisoformat(str(stamp)[:10])
    except ValueError:
        return None
    return f"{parsed.day} {parsed:%b %Y}"


def freshness(store) -> str:
    """How current the data is, for a reader who cannot refresh it themselves.

    The pages previously told everyone to run ``mcfinex prices``, which only the
    owner can do and only against their own machine. What a visitor actually
    needs is the as-of date, so they can judge whether a figure is stale --
    prices refresh nightly and fundamentals quarterly, so the two are reported
    separately rather than collapsed into one date.
    """
    priced, scraped = store.data_freshness()
    parts = []
    if shown := _human(priced):
        parts.append(f"Prices as of {shown}")
    if shown := _human(scraped):
        parts.append(f"fundamentals to {shown}")
    if not parts:
        return "Screened from stored data."
    return " · ".join(parts) + " · prices refresh nightly"
