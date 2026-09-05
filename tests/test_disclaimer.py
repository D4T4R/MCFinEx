"""One wording, everywhere the signals surface.

disclaimer.py exists so the site, the CSV, the API and the README cannot drift
into saying different things. The phone app is another surface, and it carries a
compiled-in copy because the Alerts screen is reachable before anything has been
fetched -- a disclaimer that disappears with the network is not a disclaimer.

A copy is a drift risk, so it is asserted rather than trusted.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mcfinex.disclaimer import CSV_HEADER, FULL, SHORT

APP_DISCLAIMER = Path(__file__).resolve().parents[1] / "app" / "src" / "disclaimer.ts"


def app_copy() -> str:
    """The string literal out of the app's disclaimer module."""
    source = APP_DISCLAIMER.read_text(encoding="utf-8")
    match = re.search(r"export const FULL_DISCLAIMER = (\".*\");", source, re.DOTALL)
    assert match, "could not find FULL_DISCLAIMER in the app's disclaimer module"
    return json.loads(match.group(1))


@pytest.mark.skipif(not APP_DISCLAIMER.exists(), reason="app not checked out")
class TestTheAppSaysTheSameThing:
    def test_the_compiled_copy_matches_the_source_of_truth(self):
        # Regenerate with:
        #   python -c "from mcfinex.disclaimer import FULL; ..."  (see README)
        assert app_copy() == FULL

    def test_it_still_refuses_to_call_itself_advice(self):
        copy = app_copy()
        assert "not investment advice" in copy.lower()
        assert "SEBI-registered" in copy


class TestWordingIsShared:
    def test_the_short_form_appears_in_the_long_one_in_spirit(self):
        # Both must tell the reader to do their own research; the short form is
        # what most people will actually read.
        assert "own research" in SHORT.lower()
        assert "own research" in FULL.lower()

    def test_the_csv_header_is_commented_so_it_survives_a_spreadsheet(self):
        assert CSV_HEADER.startswith("#")

    def test_none_of_them_tell_the_reader_to_run_a_command(self):
        for text in (SHORT, FULL, CSV_HEADER):
            assert "mcfinex " not in text
