"""The static site the phone app reads.

The app is sideloaded, so a payload change cannot be pushed to anyone. These
tests pin the things an installed build would break on: the filename rule, the
schema marker, and the refusal to overwrite a good site with an empty one.
"""

from __future__ import annotations

import json

import pytest

from mcfinex.db.store import Store
from mcfinex.publish import (
    SCHEMA, TREND_LABELS, company_payload, file_id, index_payload, money,
    moneys, pick_payload, trend_payload, write_site,
)
from mcfinex.picks import Tier, to_pick

from .test_picks import make_row


def _one_tiered_one_not():
    """A company the app will show, and one it will not.

    Reaching Tier.NONE needs a company that is above its entry price *and* has
    too little upside for the watch list -- not simply a bad one, because a
    weak company that is cheap still lands in a tier.
    """
    return [
        make_row(ticker="TIERED", price=100.0, target=200.0),
        make_row(ticker="UNTIERED", price=190.0, target=200.0, upside=5.0, buys=0),
    ]


class TestFileId:
    def test_a_plain_ticker_is_unchanged(self):
        assert file_id("WELCORP") == "WELCORP"

    def test_hyphens_survive_because_they_are_url_safe(self):
        assert file_id("BAJAJ-AUTO") == "BAJAJ-AUTO"

    def test_ampersands_are_mapped_out(self):
        assert file_id("M&M") == "M_M"
        assert file_id("J&KBANK") == "J_KBANK"

    def test_the_mapping_is_injective_over_real_tickers(self):
        # `_` never occurs in an NSE ticker, so mapping `&` onto it cannot
        # collide with anything already listed. If that ever stops being true
        # the publish fails loudly rather than serving one company as another.
        real = ["M&M", "M&MFIN", "ARE&M", "J&KBANK", "GMRP&UI", "GVT&D",
                "BAJAJ-AUTO", "NAM-INDIA", "MCCHRLS-B", "WELCORP"]
        assert len({file_id(t) for t in real}) == len(real)


class TestRounding:
    def test_money_keeps_two_decimals(self):
        assert money(78.49408469961412) == 78.49

    def test_money_passes_none_through(self):
        assert money(None) is None

    def test_moneys_handles_a_series_with_gaps(self):
        assert moneys([1.005, None, 3.14159]) == [1.0, None, 3.14]

    def test_a_price_does_not_serialise_as_float_noise(self):
        payload = pick_payload(to_pick(make_row(price=100.0, target=200.0)))
        text = json.dumps(payload)
        assert "78.49408469961412" not in text
        # Every float in the payload is short enough to be a displayed number.
        for value in payload.values():
            if isinstance(value, float):
                assert len(repr(value).split(".")[-1]) <= 2


class TestPickPayload:
    def test_it_carries_the_fields_the_app_renders(self):
        payload = pick_payload(to_pick(make_row()))
        for key in ("ticker", "name", "sector", "price", "target", "tier",
                    "upside_pct", "discount_to_entry_pct", "buy_signals",
                    "quality_buys", "models_agreeing", "flags"):
            assert key in payload

    def test_tier_is_sent_as_its_label_not_the_enum(self):
        payload = pick_payload(to_pick(make_row(price=100.0, target=200.0)))
        assert payload["tier"] == Tier.HIGH_CONVICTION.value
        assert isinstance(payload["tier"], str)


class TestTrendPayload:
    def test_no_history_produces_nothing_rather_than_an_empty_shell(self):
        assert trend_payload("Sales", []) is None

    def test_yoy_growth_is_a_series_not_a_scalar(self):
        history = [(f"202{y}-{m:02d}-01", 100.0 + i * 10)
                   for i, (y, m) in enumerate(
                       [(3, 3), (3, 6), (3, 9), (3, 12), (4, 3), (4, 6)])]
        payload = trend_payload("Sales", history)
        # Aligned to periods[4:], so it is shorter than the value series.
        assert isinstance(payload["yoy_growth_pct"], list)
        assert len(payload["yoy_growth_pct"]) < len(payload["values"])


class TestIndexPayload:
    def test_universe_counts_everything_screened_not_just_the_tiered(self):
        payload = index_payload(_one_tiered_one_not(), priced="2026-09-04",
                                scraped="2026-08-21")
        assert payload["universe"] == 2
        # The untiered row is counted in `tiers` but is not offered as a pick.
        assert payload["tiers"][Tier.NONE.value] == 1
        assert [p["ticker"] for p in payload["picks"]] == ["TIERED"]

    def test_every_pick_carries_the_filename_to_fetch(self):
        payload = index_payload([make_row()], priced=None, scraped=None)
        pick = payload["picks"][0]
        assert pick["id"] == file_id(pick["ticker"])

    def test_it_reports_the_freshness_dates_it_was_given(self):
        payload = index_payload([make_row()], priced="2026-09-04", scraped="2026-08-21")
        assert payload["price_date"] == "2026-09-04"
        assert payload["last_scraped"] == "2026-08-21"

    def test_it_is_stamped_with_a_schema_the_app_can_check(self):
        payload = index_payload([make_row()], priced=None, scraped=None)
        assert payload["schema"] == SCHEMA

    def test_generated_is_utc_so_it_reads_the_same_everywhere(self):
        payload = index_payload([make_row()], priced=None, scraped=None)
        assert payload["generated"].endswith("Z")


class FakeStore:
    """Enough Store for write_site: freshness and bulk trend history."""

    def __init__(self, histories=None):
        self.histories = histories or {}
        self.asked_for = None

    def data_freshness(self):
        return ("2026-09-04", "2026-08-21")

    def all_quarterly_history(self, labels):
        self.asked_for = list(labels)
        return self.histories


class TestWriteSite:
    def test_it_writes_an_index_and_one_file_per_tiered_company(self, tmp_path):
        written = write_site(FakeStore(), tmp_path, _one_tiered_one_not())

        assert (tmp_path / "index.json").exists()
        files = list((tmp_path / "company").glob("*.json"))
        # The untiered row reaches no screen in the app, so it gets no page.
        assert [f.stem for f in files] == ["TIERED"]
        assert written.companies == 1

    def test_the_detail_file_is_named_by_file_id(self, tmp_path):
        row = make_row(price=100.0, target=200.0)
        row.screening.ticker = "M&M"
        write_site(FakeStore(), tmp_path, [row])
        assert (tmp_path / "company" / "M_M.json").exists()

    def test_the_detail_file_records_the_real_ticker(self, tmp_path):
        row = make_row(price=100.0, target=200.0)
        row.screening.ticker = "M&M"
        write_site(FakeStore(), tmp_path, [row])
        payload = json.loads((tmp_path / "company" / "M_M.json").read_text())
        assert payload["ticker"] == "M&M"

    def test_history_is_fetched_once_for_everyone_not_per_company(self, tmp_path):
        # The N+1 this exists to avoid: per company per label is thousands of
        # round-trips against a hosted database.
        store = FakeStore()
        write_site(store, tmp_path, [make_row() for _ in range(5)])
        assert store.asked_for == list(TREND_LABELS)

    def test_a_colliding_filename_refuses_to_publish(self, tmp_path):
        first, second = make_row(price=100.0, target=200.0), make_row(price=100.0, target=200.0)
        first.screening.ticker = "A&B"
        second.screening.ticker = "A_B"
        with pytest.raises(ValueError, match="both publish as"):
            write_site(FakeStore(), tmp_path, [first, second])

    def test_json_is_compact_because_whitespace_is_a_third_of_the_transfer(self, tmp_path):
        write_site(FakeStore(), tmp_path, [make_row()])
        assert "\n" not in (tmp_path / "index.json").read_text()


class TestPublishedSiteAgainstARealDatabase:
    """Round-trip through an actual Store, so the SQL is exercised too."""

    @pytest.fixture
    def store(self, tmp_path):
        with Store(tmp_path / "test.db") as store:
            store.create_schema()
            store.upsert_company("ACME", {
                "name": "Acme Ltd", "sector": "Widgets", "current_price": 100.0,
                "price_date": "2026-09-04", "last_updated": "2026-08-21",
            })
            # Deliberately inserted newest first, so the ordering assertion is
            # testing the query rather than the insertion order.
            store.replace_financials("ACME", [
                (period, "quarters", "Sales", value)
                for period, value in reversed([
                    ("2025-03-01", 100.0), ("2025-06-01", 110.0),
                    ("2025-09-01", 120.0), ("2025-12-01", 130.0),
                    ("2026-03-01", 140.0), ("2026-06-01", 150.0),
                ])
            ])
            yield store

    def test_bulk_history_returns_periods_and_values_per_company(self, store):
        history = store.all_quarterly_history(["Sales"])
        assert history["ACME"]["Sales"][0] == ("2025-03-01", 100.0)
        # Oldest first, as trends.analyse requires.
        assert [p for p, _ in history["ACME"]["Sales"]] == sorted(
            p for p, _ in history["ACME"]["Sales"])

    def test_bulk_history_of_nothing_asks_the_database_nothing(self, store):
        assert store.all_quarterly_history([]) == {}

    def test_company_payload_builds_trends_from_the_bulk_map(self, store):
        history = store.all_quarterly_history(TREND_LABELS)
        payload = company_payload(make_row(), history.get("ACME", {}))
        assert [t["label"] for t in payload["trends"]] == ["Sales"]
        assert payload["schema"] == SCHEMA


class TestScreenPayload:
    """The web screener's own file.

    Separate from index.json because the fundamentals a chart plots on took that
    file from 94 KB gzipped to 139 KB -- a 48% rise paid by every phone on mobile
    data to feed charts only a desktop draws. The app is sideloaded and cannot be
    force-updated, so holding its payload still is worth more than the bytes.
    """

    def test_the_phone_app_payload_is_not_widened(self):
        # The contract that matters most here. An installed build reads
        # index.json, and no update can be pushed to whoever is holding one.
        from mcfinex.publish import SCREEN_FIELDS

        payload = index_payload([make_row()], priced="2026-09-30", scraped="2026-08-19")
        for pick in payload["picks"]:
            for field in (*SCREEN_FIELDS, "price_to_book"):
                assert field not in pick, field

    def test_it_covers_the_whole_universe_not_only_the_shortlist(self):
        from mcfinex.publish import screen_payload

        rows = _one_tiered_one_not()
        payload = screen_payload(rows)
        # index.json ranks; this is a cross-section. A scatter drawn with the
        # untiered companies removed shows the shape of the shortlist while
        # looking like the shape of the market.
        assert len(payload["companies"]) == len(rows)
        assert {c["ticker"] for c in payload["companies"]} == {"TIERED", "UNTIERED"}

    def test_the_two_files_describe_the_same_night(self):
        from mcfinex.publish import index_payload as idx, screen_payload

        rows = [make_row()]
        stamp = "2026-09-30T18:00:00Z"
        a = idx(rows, priced="2026-09-30", scraped="2026-08-19", generated=stamp)
        b = screen_payload(rows, priced="2026-09-30", scraped="2026-08-19",
                           generated=stamp)
        for key in ("generated", "price_date", "last_scraped", "universe", "tiers"):
            assert a[key] == b[key], key

    def test_pick_fields_cannot_drift_between_the_two(self):
        # Both go through pick_payload, so a field cannot come to mean one thing
        # in the app and another on the web.
        from mcfinex.publish import screen_payload

        rows = [make_row()]
        in_index = index_payload(rows, priced=None, scraped=None)["picks"][0]
        in_screen = screen_payload(rows)["companies"][0]
        for key, value in in_index.items():
            assert in_screen[key] == value, key

    def test_the_fundamentals_a_chart_needs_are_present(self):
        from mcfinex.publish import SCREEN_FIELDS, screen_payload

        company = screen_payload([make_row()])["companies"][0]
        for field in SCREEN_FIELDS:
            assert field in company, field

    def test_market_cap_reaches_the_payload(self):
        # It is the context a reader needs most, and until now the screening
        # pipeline dropped it: Metrics had no such field.
        from mcfinex.publish import screen_payload

        row = make_row(market_cap=1785744.0)
        assert screen_payload([row])["companies"][0]["market_cap"] == 1785744.0

    def test_price_to_book_is_derived_once_here_not_in_every_client(self):
        from mcfinex.publish import screen_payload

        company = screen_payload([make_row(price=100.0, book_value=50.0)])["companies"][0]
        assert company["price_to_book"] == 2.0

    def test_a_worthless_book_value_does_not_become_an_infinity(self):
        # A zero or negative book value is common among the companies this screen
        # flags. Divided in the browser it renders an infinity onto a chart axis
        # and takes the whole scale with it.
        from mcfinex.publish import screen_payload

        for book in (0.0, -12.0, None):
            row = make_row(price=100.0, book_value=book)
            got = screen_payload([row])["companies"][0]["price_to_book"]
            assert got is None, (book, got)

    def test_it_serialises_without_nan(self):
        # allow_nan=False, so a NaN anywhere raises at publish rather than
        # reaching a client as the token NaN, which JSON.parse rejects.
        from mcfinex.publish import screen_payload

        json.dumps(screen_payload(_one_tiered_one_not()),
                   separators=(",", ":"), allow_nan=False)

    def test_the_file_is_written_alongside_the_app_payload(self, tmp_path):
        from mcfinex.publish import write_site

        write_site(FakeStore(), tmp_path, _one_tiered_one_not())
        assert (tmp_path / "screen.json").exists()
        assert (tmp_path / "index.json").exists()
