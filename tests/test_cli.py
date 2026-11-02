"""Which companies a scrape decides to visit.

The selection is the part worth pinning. A wrong list is silent: the run goes
green, the log says it scraped what it chose to scrape, and the companies it
quietly dropped look exactly like the ones it never had to touch.
"""

from __future__ import annotations

import argparse

import pytest

from mcfinex import cli
from mcfinex.db.store import Store
from mcfinex.sources import screener


QUARTER = "2026-09"


@pytest.fixture
def db(tmp_path):
    """A universe shaped like the one an interrupted forced re-scrape leaves.

    DONE_* carry the interrupted run's date. PENDING_* were never reached and
    still hold figures that are *current but wrong* -- the stored quarter is
    this quarter, so nothing in `needs_refresh` can tell they are stale.
    """
    path = tmp_path / "cli.db"
    with Store(path) as store:
        store.create_schema()
        for ticker in ("DONE_A", "DONE_B"):
            store.upsert_company(ticker, {"last_updated": "2026-10-31",
                                          "last_updated_quarter": QUARTER})
        for ticker in ("PENDING_A", "PENDING_B"):
            store.upsert_company(ticker, {"last_updated": "2026-08-20",
                                          "last_updated_quarter": QUARTER})
    return path


def run_scrape(db, monkeypatch, **overrides):
    """Run cmd_scrape with the network stubbed, returning the tickers it tried.

    `fetch` raises for every ticker, which the loop counts as a failure and
    carries past -- so the call list is a faithful record of what was selected
    without needing to stub parsing or the writes.
    """
    attempted: list[str] = []

    def fake_fetch(ticker, **kwargs):
        attempted.append(ticker)
        raise screener.ScreenerError(f"stubbed: {ticker}")

    monkeypatch.setattr(screener, "fetch", fake_fetch)
    monkeypatch.setattr(cli, "current_quarter", lambda: QUARTER)

    args = argparse.Namespace(
        db=str(db), tickers=[], all=True, from_template=False, template="",
        consolidated=False, force=False, stale_before=None, limit=None,
        missing=None,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    cli.cmd_scrape(args)
    return attempted


class TestStaleBefore:
    def test_it_visits_only_what_predates_the_cutoff(self, db, monkeypatch):
        attempted = run_scrape(db, monkeypatch, stale_before="2026-10-31")
        assert attempted == ["PENDING_A", "PENDING_B"]

    def test_the_cutoff_overrides_the_freshness_check(self, db, monkeypatch):
        """The coupling that makes the flag work at all.

        These companies carry the current quarter, so `needs_refresh` returns
        False for every one of them. Without the cutoff implying the force, the
        command selects the right two and then scrapes neither -- a run that
        ends green having done nothing, which is the hardest kind to notice.
        """
        with Store(db) as store:
            for ticker in ("PENDING_A", "PENDING_B"):
                assert store.needs_refresh(ticker, QUARTER) is False

        assert run_scrape(db, monkeypatch, stale_before="2026-10-31")

    def test_without_it_a_plain_all_run_does_nothing_here(self, db, monkeypatch):
        # The situation that made the flag necessary: every company looks
        # current, so `--all` alone is a no-op.
        assert run_scrape(db, monkeypatch) == []

    def test_force_alone_redoes_the_finished_companies(self, db, monkeypatch):
        # Why resuming with --force is not an option: it starts from the top.
        assert run_scrape(db, monkeypatch, force=True) == [
            "DONE_A", "DONE_B", "PENDING_A", "PENDING_B"]

    def test_a_cutoff_that_matches_nothing_is_not_an_error(self, db, monkeypatch):
        # Re-dispatching after the resume finished should be a cheap no-op,
        # not a red run.
        assert run_scrape(db, monkeypatch, stale_before="2020-01-01") == []
