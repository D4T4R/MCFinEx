"""Merging two exchanges into one price update.

Three properties, each of which fails silently if it breaks -- a price update
only touches companies it already knows, so a bad match writes nothing and a
wrong match writes a wrong number. Neither raises.

  - One write per company per run, even though 2,395 of 2,543 NSE listings also
    traded on BSE on 2026-09-07.
  - NSE wins wherever both have a close.
  - BSE rows are matched on ISIN, never on ticker, because the symbol belongs to
    the other exchange's namespace.
"""

from __future__ import annotations

from mcfinex import prices
from mcfinex.db.store import Store
from mcfinex.sources.bhavcopy import Listing


def nse(ticker, isin, close=100.0):
    return Listing(ticker=ticker, isin=isin, name=f"{ticker} Ltd",
                   close=close, exchange="NSE")


def bse(ticker, isin, close=200.0):
    return Listing(ticker=ticker, isin=isin, name=f"{ticker} Ltd",
                   close=close, exchange="BSE")


class TestPrecedence:
    def test_nse_wins_when_both_traded(self):
        merged = prices.merge([nse("ACME", "INE1", 101.0)],
                              [bse("ACME", "INE1", 999.0)],
                              {"INE1": "ACME"})
        assert merged.prices == {"ACME": 101.0}

    def test_one_write_per_company(self):
        # The "do not run twice for the same stock" requirement.
        merged = prices.merge([nse("ACME", "INE1")], [bse("ACME", "INE1")],
                              {"INE1": "ACME"})
        assert len(merged.prices) == 1
        assert merged.by_exchange == {"NSE": ["ACME"]}

    def test_bse_fills_a_gap_nse_left(self):
        # ACME did not trade on NSE that day; BETA did not trade at all.
        merged = prices.merge([nse("BETA", "INE2", 55.0)],
                              [bse("ACMEBSE", "INE1", 42.0)],
                              {"INE1": "ACME", "INE2": "BETA"})
        assert merged.prices == {"BETA": 55.0, "ACME": 42.0}
        assert merged.by_exchange["BSE"] == ["ACME"]

    def test_the_summary_names_both_exchanges(self):
        merged = prices.merge([nse("BETA", "INE2")], [bse("X", "INE1")],
                              {"INE1": "ACME", "INE2": "BETA"})
        assert "BSE 1" in merged.summary() and "NSE 1" in merged.summary()


class TestMatching:
    def test_a_bse_row_is_matched_by_isin_not_symbol(self):
        # The company is stored as ACME; BSE calls it ACMELTD. Matching on the
        # symbol would miss it, and missing writes nothing and says nothing.
        merged = prices.merge([], [bse("ACMELTD", "INE1", 42.0)], {"INE1": "ACME"})
        assert merged.prices == {"ACME": 42.0}

    def test_a_bse_symbol_colliding_with_a_different_company_is_not_written(self):
        # BSE's "BETA" is a different security from the stored BETA. A ticker
        # match here would write one company's price onto another.
        merged = prices.merge([], [bse("BETA", "INE-OTHER", 7.0)],
                              {"INE2": "BETA"})
        assert merged.prices == {}
        assert merged.unmatched == 1

    def test_an_nse_company_with_no_stored_isin_is_still_priced(self):
        # 21 stored companies have no ISIN, so they are absent from the ISIN map
        # entirely -- that is the point. An earlier version resolved primary rows
        # through the map first and stopped pricing them, and because a price
        # update only touches rows it finds, nothing raised.
        merged = prices.merge([nse("NOISIN", "INE_NOISIN", 12.0)], [],
                              {"INE_OTHER": "ACME"})
        assert merged.prices == {"NOISIN": 12.0}

    def test_an_unknown_nse_ticker_is_written_when_the_roster_is_unknown(self):
        # Without known_tickers the merge cannot tell a new listing from a
        # tracked one, so it passes it through -- exactly as the single-exchange
        # version did, where UPDATE ... WHERE ticker = ? simply matched no row.
        merged = prices.merge([nse("NEWLISTING", "INE9")], [], {"INE1": "ACME"})
        assert merged.prices == {"NEWLISTING": 100.0}

    def test_an_unknown_nse_ticker_is_counted_when_the_roster_is_known(self):
        merged = prices.merge([nse("NEWLISTING", "INE9")], [], {"INE1": "ACME"},
                              known_tickers={"ACME"})
        assert merged.prices == {}
        assert merged.unmatched == 1

    def test_a_renamed_nse_symbol_is_followed_through_its_isin(self):
        # The exchange renamed ACME to ACMENEW; the database still says ACME.
        merged = prices.merge([nse("ACMENEW", "INE1", 33.0)], [],
                              {"INE1": "ACME"}, known_tickers={"ACME"})
        assert merged.prices == {"ACME": 33.0}

    def test_a_listing_with_no_close_is_skipped(self):
        merged = prices.merge([nse("ACME", "INE1", None)],
                              [bse("ACME", "INE1", 42.0)],
                              {"INE1": "ACME"})
        # NSE had no price, so BSE's fills it rather than being blocked.
        assert merged.prices == {"ACME": 42.0}

    def test_neither_exchange_having_a_close_writes_nothing(self):
        merged = prices.merge([nse("ACME", "INE1", None)],
                              [bse("ACME", "INE1", None)], {"INE1": "ACME"})
        assert merged.prices == {}

    def test_duplicate_rows_within_one_feed_take_the_first(self):
        merged = prices.merge([nse("ACME", "INE1", 1.0), nse("ACME", "INE1", 2.0)],
                              [], {"INE1": "ACME"})
        assert merged.prices == {"ACME": 1.0}


class TestNewListings:
    def test_a_company_already_tracked_is_not_seeded_again(self):
        # Listed on both exchanges under different symbols. Matching on ticker
        # would create a second row for one company.
        assert prices.new_listings({"INE1"}, [bse("ACMELTD", "INE1")]) == []

    def test_a_genuinely_new_isin_is_kept(self):
        fresh = prices.new_listings({"INE1"}, [bse("ONLYBSE", "INE2")])
        assert [l.ticker for l in fresh] == ["ONLYBSE"]

    def test_duplicates_within_the_candidates_are_collapsed(self):
        fresh = prices.new_listings(set(), [bse("A", "INE2"), bse("A2", "INE2")])
        assert len(fresh) == 1

    def test_a_listing_with_no_isin_is_never_seeded(self):
        # Without an ISIN there is no way to tell it apart from a company already
        # stored, so seeding it risks a duplicate that nothing can reconcile.
        assert prices.new_listings(set(), [bse("MYSTERY", "")]) == []


class TestBseIsBestEffort:
    """A BSE problem must cost the BSE rows, not the run.

    NSE has already succeeded by the time BSE is asked, so the run is holding a
    complete set of prices for everything NSE lists. Letting a BSE outage abort
    it would trade a full refresh for no refresh.
    """

    def _day(self):
        from datetime import date
        return date(2026, 9, 7)

    def test_an_outage_degrades_to_nse_alone(self, monkeypatch):
        import requests as rq

        from mcfinex import cli
        from mcfinex.sources import bse as bse_mod

        def boom(**kwargs):
            raise rq.ConnectionError("bse is down")

        monkeypatch.setattr(bse_mod, "latest_bhavcopy", boom)
        assert cli._bse_listings_for(self._day(), None) == []

    def test_a_bse_error_degrades_to_nse_alone(self, monkeypatch):
        from mcfinex import cli
        from mcfinex.sources import bse as bse_mod

        def boom(**kwargs):
            raise bse_mod.BseError("nothing in the last 10 days")

        monkeypatch.setattr(bse_mod, "latest_bhavcopy", boom)
        assert cli._bse_listings_for(self._day(), None) == []

    def test_a_different_session_is_refused(self, monkeypatch):
        # BSE's walk-back would happily return Friday for a Monday request.
        # Writing Friday's BSE close under Monday's price_date would make the
        # staleness invisible, which is worse than the gap.
        from datetime import date

        from mcfinex import cli
        from mcfinex.sources import bse as bse_mod

        monkeypatch.setattr(bse_mod, "latest_bhavcopy",
                            lambda **kw: (date(2026, 9, 4), b"TradDt,ISIN\n"))
        assert cli._bse_listings_for(self._day(), None) == []

    def test_the_matching_session_is_used(self, monkeypatch):
        from mcfinex import cli
        from mcfinex.sources import bse as bse_mod

        payload = b"x"
        monkeypatch.setattr(bse_mod, "latest_bhavcopy", lambda **kw: (self._day(), payload))
        monkeypatch.setattr(bse_mod, "parse_bhavcopy", lambda p: [bse("ACME", "INE1")])
        assert [l.ticker for l in cli._bse_listings_for(self._day(), None)] == ["ACME"]


class TestAgainstARealDatabase:
    """The ISIN lookup the merge depends on, through actual SQL."""

    def test_tickers_by_isin_round_trips(self, tmp_path):
        with Store(tmp_path / "s.db") as store:
            store.create_schema()
            store.upsert_company("ACME", {"isin": "INE1", "last_updated": "2026-09-01"})
            store.upsert_company("BETA", {"isin": "INE2", "last_updated": "2026-09-01"})
            assert store.tickers_by_isin() == {"INE1": "ACME", "INE2": "BETA"}

    def test_companies_without_an_isin_are_left_out_of_the_map(self, tmp_path):
        with Store(tmp_path / "s.db") as store:
            store.create_schema()
            store.upsert_company("NOISIN", {"last_updated": "2026-09-01"})
            store.upsert_company("BLANK", {"isin": "", "last_updated": "2026-09-01"})
            assert store.tickers_by_isin() == {}

    def test_an_ambiguous_isin_is_not_matchable(self, tmp_path):
        # HEG and HEGAM both hold INE545A01024: the seeder keys on ticker, so a
        # renamed company gains a row. Picking either is a guess, and guessing
        # wrong writes today's price onto the row the exchange has stopped
        # feeding, where it looks entirely current.
        with Store(tmp_path / "s.db") as store:
            store.create_schema()
            store.upsert_company("HEG", {"isin": "INE545A01024"})
            store.upsert_company("HEGAM", {"isin": "INE545A01024"})
            store.upsert_company("ACME", {"isin": "INE1"})
            assert store.tickers_by_isin() == {"INE1": "ACME"}
            assert store.duplicate_isins() == {"INE545A01024": ["HEG", "HEGAM"]}

    def test_a_bse_row_for_an_ambiguous_isin_writes_nothing(self, tmp_path):
        with Store(tmp_path / "s.db") as store:
            store.create_schema()
            store.upsert_company("HEG", {"isin": "INE545A01024",
                                         "last_updated": "2026-08-19"})
            store.upsert_company("HEGAM", {"isin": "INE545A01024"})
            merged = prices.merge([], [bse("HEG", "INE545A01024", 739.0)],
                                  store.tickers_by_isin())
            assert merged.prices == {}
            assert merged.unmatched == 1

    def test_known_isins_includes_the_merely_seeded(self, tmp_path):
        # A row seeded last night but not yet scraped is still a row, so seeding
        # it again would duplicate it.
        with Store(tmp_path / "s.db") as store:
            store.create_schema()
            store.upsert_company("SEEDED", {"isin": "INE1"})   # no last_updated
            assert store.known_isins() == {"INE1"}

    def test_a_bse_only_company_gets_priced_end_to_end(self, tmp_path):
        with Store(tmp_path / "s.db") as store:
            store.create_schema()
            store.upsert_company("ONLYBSE", {"isin": "INE9", "last_updated": "2026-09-01"})
            merged = prices.merge([], [bse("ONLYBSE-B", "INE9", 77.5)],
                                  store.tickers_by_isin())
            assert store.update_prices(merged.prices, "2026-09-07") == 1
            assert store.company("ONLYBSE")["current_price"] == 77.5


class TestFindRenames:
    """Inferring that two symbols are one company.

    The bar is high on purpose. A wrong inference merges two companies'
    histories into a single row, and no later run can take them apart again --
    the evidence that they were ever separate is what got deleted.
    """

    def feed(self, *pairs):
        return [nse(t, i) for t, i in pairs]

    def test_a_stored_symbol_absent_from_the_feed_is_a_rename(self):
        renames, ambiguous = prices.find_renames(
            self.feed(("HEGAM", "INE545")), {"INE545": ["HEG"]}, {"HEGAM"})
        assert [(r.old, r.new) for r in renames] == [("HEG", "HEGAM")]
        assert ambiguous == {}

    def test_the_forked_pair_is_reconciled(self):
        # The state the seeder actually leaves behind: both rows present.
        renames, ambiguous = prices.find_renames(
            self.feed(("HEGAM", "INE545")), {"INE545": ["HEG", "HEGAM"]}, {"HEGAM"})
        assert [(r.old, r.new) for r in renames] == [("HEG", "HEGAM")]
        assert ambiguous == {}

    def test_a_symbol_still_trading_is_never_treated_as_an_old_name(self):
        # Two live symbols sharing an ISIN is not a rename, whatever else is
        # true. Renaming one onto the other would delete a real company.
        renames, ambiguous = prices.find_renames(
            self.feed(("HEGAM", "INE545")), {"INE545": ["HEG", "HEGAM"]},
            {"HEG", "HEGAM"})
        assert renames == []
        assert ambiguous == {"INE545": ["HEG", "HEGAM"]}

    def test_an_isin_absent_from_the_feed_is_left_alone(self):
        # Suspended or delisted. There is no new name to move to.
        renames, ambiguous = prices.find_renames([], {"INE545": ["HEG"]}, set())
        assert renames == []
        assert ambiguous == {}

    def test_a_duplicate_with_no_feed_row_is_reported_not_guessed(self):
        renames, ambiguous = prices.find_renames([], {"INE545": ["HEG", "HEGAM"]}, set())
        assert renames == []
        assert ambiguous == {"INE545": ["HEG", "HEGAM"]}

    def test_an_unchanged_symbol_produces_nothing(self):
        renames, _ = prices.find_renames(
            self.feed(("ACME", "INE1")), {"INE1": ["ACME"]}, {"ACME"})
        assert renames == []

    def test_a_listing_with_no_isin_cannot_rename_anything(self):
        renames, _ = prices.find_renames(
            [nse("MYSTERY", "")], {"INE1": ["ACME"]}, {"MYSTERY"})
        assert renames == []


class TestRenameTicker:
    """Moving a company across four tables without losing any of it."""

    def _company(self, store, ticker, isin, *, history=True):
        store.upsert_company(ticker, {"isin": isin, "name": f"{ticker} Ltd",
                                      "current_price": 100.0,
                                      "last_updated": "2026-08-19"})
        if history:
            store.replace_financials(ticker, [("2026-03-01", "quarters", "Sales", 42.0)])
            store.replace_valuations(ticker, "ev_ebitda", {"target": 200.0})

    def test_history_follows_the_new_symbol(self, tmp_path):
        with Store(tmp_path / "s.db") as store:
            store.create_schema()
            self._company(store, "HEG", "INE545")
            store.rename_ticker("HEG", "HEGAM")

            assert store.company("HEG") is None
            assert store.company("HEGAM")["isin"] == "INE545"
            assert store.series("HEGAM", "quarters", "Sales") == [42.0]
            assert store.valuation_fields("HEGAM", "ev_ebitda") == {"target": 200.0}

    def test_the_empty_row_the_seeder_made_is_replaced(self, tmp_path):
        with Store(tmp_path / "s.db") as store:
            store.create_schema()
            self._company(store, "HEG", "INE545")
            store.upsert_company("HEGAM", {"isin": "INE545"})   # bare, from seeding
            store.rename_ticker("HEG", "HEGAM")

            assert store.isin_groups() == {"INE545": ["HEGAM"]}
            assert store.series("HEGAM", "quarters", "Sales") == [42.0]

    def test_alert_state_moves_too(self, tmp_path):
        # Left behind, the renamed company looks new to the alert rules and
        # fires an entry-reached notification for a price that has not moved.
        from mcfinex.alerts import Snapshot

        with Store(tmp_path / "s.db") as store:
            store.create_schema()
            self._company(store, "HEG", "INE545")
            store.save_alert_state({"HEG": Snapshot("high_conviction", True, 40.0).as_dict()})
            store.rename_ticker("HEG", "HEGAM")

            state = store.alert_state()
            assert "HEG" not in state
            assert state["HEGAM"]["tier"] == "high_conviction"

    def test_every_ticker_keyed_table_is_covered(self, tmp_path):
        # The guard against adding a table and forgetting this list: any table
        # with a ticker column has to be in TICKER_TABLES or its rows are
        # stranded under a symbol that no longer exists.
        with Store(tmp_path / "s.db") as store:
            store.create_schema()
            keyed = {
                t for t in ("companies", "financials", "valuations",
                            "result_calendar", "alert_state")
                if any(c[1] == "ticker"
                       for c in store.conn.execute(f"PRAGMA table_info({t})").fetchall())
            }
            assert keyed - {"companies"} == set(Store.TICKER_TABLES)

    def test_renaming_to_itself_is_a_no_op(self, tmp_path):
        with Store(tmp_path / "s.db") as store:
            store.create_schema()
            self._company(store, "HEG", "INE545")
            store.rename_ticker("HEG", "HEG")
            assert store.series("HEG", "quarters", "Sales") == [42.0]

    def test_has_history_distinguishes_the_two_rows(self, tmp_path):
        with Store(tmp_path / "s.db") as store:
            store.create_schema()
            self._company(store, "HEG", "INE545")
            store.upsert_company("HEGAM", {"isin": "INE545"})
            assert store.has_history("HEG")
            assert not store.has_history("HEGAM")
