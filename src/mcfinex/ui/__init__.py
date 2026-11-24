"""Shared presentation helpers for the Streamlit pages."""

from __future__ import annotations

import re
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


#: Screener opens most descriptions with the year of incorporation, which is
#: the least interesting thing it could lead with.
_FOUNDED = re.compile(r"^(?:incorporated|established|founded|set\s+up)\s+in\s+\d{4}\s*,?\s*",
                      re.IGNORECASE)
#: ...then restates the company name the reader just read in the heading.
_RESTATES_NAME = re.compile(
    r"^(?:the\s+)?[\w&.,'’\-() ]{3,60}?\s+(?:limited|ltd\.?)\s*(?:\([A-Z]{2,6}\)\s*)?"
    r"(?:is|was)\s+(?:an?|the)?\s*",
    re.IGNORECASE)
#: Applied repeatedly, because these stack: "is engaged in the business of
#: owning..." strips to "the business of owning..." on one pass and needs a
#: second to reach the verb. Each alternative is a whole phrase, so looping
#: cannot eat into the description itself.
_LEFTOVER = re.compile(
    r"^(?:is\s+|was\s+)?(?:engaged\s+in|in\s+the\s+business\s+of|the\s+business\s+of"
    r"|primarily\s+|mainly\s+)\s*",
    re.IGNORECASE)
#: Screener footnotes its description with a bracketed source marker. The
#: scraper strips it, but this does not assume the text came from the scraper:
#: a row written before that existed still carries one.
_FOOTNOTE = re.compile(r"\s*\[\d+\]\s*")


def business_summary(about: str | None) -> str | None:
    """What a company actually does, with screener's preamble removed.

    The stored text is verbatim, and most of it opens the same way: "Incorporated
    in 1999, Credo Brands Marketing Ltd is in the business of selling of fashion
    casual garments...". The year is rarely the point and the name is already the
    heading, so two thirds of a short blurb can be throat-clearing.

    Stripping is conservative on purpose. Each pattern is anchored at the start
    and only removes a preamble it can recognise in full, so the failure mode is
    leaving a few words in rather than cutting into the substance -- this is the
    one place in the app where the text is the company's own claim about itself,
    and truncating that to read better would be editing it.

    Returns None for nothing to show, so a caller can omit the block entirely
    rather than render an empty panel.
    """
    if not about or not about.strip():
        return None
    text = _FOOTNOTE.sub(" ", " ".join(about.split())).strip()
    text = _FOUNDED.sub("", text)
    text = _RESTATES_NAME.sub("", text)
    for _ in range(3):
        stripped = _LEFTOVER.sub("", text)
        if stripped == text:
            break
        text = stripped
    text = text.strip(" ,;")
    if not text:
        return " ".join(about.split())
    return text[0].upper() + text[1:]


def _short(stamp: str | None) -> str | None:
    """``2026-11-20`` as ``20/11/26``; ``None`` if it is not a date.

    For a date that has to sit in a row of counts. The long form is close to
    three times the width of the numbers beside it, which makes a tidy grid
    look ragged. Prose keeps :func:`_human` -- "20/11/26" reads as a date you
    have to parse, where "20 Nov 2026" reads as one you can.

    Day first, which is how a date is written in India; the ISO form it comes
    from is the only one that would be read month-first here.
    """
    if not stamp:
        return None
    try:
        parsed = date.fromisoformat(str(stamp)[:10])
    except ValueError:
        return None
    return f"{parsed:%d/%m/%y}"


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
