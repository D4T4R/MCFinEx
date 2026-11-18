import pytest

from mcfinex.picks import (
    Tier, churn, classify, last_change, rank, sector_heat, to_pick,
)
from mcfinex.report import Row
from mcfinex.screening import Metrics, screen


def make_row(*, price=100.0, target=200.0, upside=60.0, buys=7, sells=0,
             sector="Widgets", newly_listed=False, financial=False,
             pe_yearly=180.0, pe_quarterly=170.0, enriched=True, quarters=12,
             **overrides):
    """A screened row with the levers the tiers depend on.

    ``buys`` walks a fixed ladder of signals, which cannot express a company
    that is sound but expensive -- the ladder makes the cheapness signals BUY
    before the last quality one. Named ``overrides`` set any Metrics field
    directly for cases the ladder cannot reach.
    """
    fields = dict(
        ticker="ACME", name="Acme Ltd", sector=sector, price=price,
        ev_ebitda_upside=upside, is_financial=financial,
        quarters_reported=0 if newly_listed else quarters,
        current_assets=150.0 if enriched else None,
        current_liabilities=100.0 if enriched else None,
        # Enough BUY signals to hit the requested count.
        roce=25.0 if buys >= 1 else 1.0,
        dividend_yield=3.0 if buys >= 2 else 0.0,
        free_cash_flow=10.0 if buys >= 3 else -10.0,
        reserves=900.0 if buys >= 4 else -1.0, equity_capital=100.0,
        book_value=200.0 if buys >= 5 else 10.0,
        promoter_holding=70.0 if buys >= 6 else 10.0,
        stock_pe=10.0 if buys >= 7 else 40.0, sector_pe=20.0,
        inventory_days=80.0 if buys >= 8 else 200.0, inventory_days_prior=100.0,
    )
    metrics = Metrics(**{**fields, **overrides})
    return Row(
        screening=screen(metrics), metrics=metrics,
        target_ev_ebitda=target, target_pe_yearly=pe_yearly,
        target_pe_quarterly=pe_quarterly,
        entry_3by4=target * 0.75 if target else None,
        entry_2by3=target * 0.66 if target else None,
    )


class TestClassify:
    def test_below_two_thirds_entry_and_corroborated_is_high_conviction(self):
        assert classify(make_row(price=100.0, target=200.0)) is Tier.HIGH_CONVICTION

    def test_above_the_entry_price_is_not_high_conviction(self):
        # target 200 -> entry 2/3 is 132; at 140 it is no longer there.
        assert classify(make_row(price=140.0, target=200.0)) is not Tier.HIGH_CONVICTION

    def test_too_few_buy_signals_demotes_it(self):
        assert classify(make_row(buys=2)) is Tier.BELOW_ENTRY

    def test_models_must_all_agree(self):
        row = make_row(pe_yearly=50.0, pe_quarterly=40.0)  # both below price
        assert classify(row) is Tier.BELOW_ENTRY

    def test_new_listings_cannot_reach_the_top_tier(self):
        # Their per-share history spans the IPO, so it cannot corroborate.
        assert classify(make_row(newly_listed=True)) is not Tier.HIGH_CONVICTION

    def test_financials_cannot_reach_the_top_tier(self):
        assert classify(make_row(financial=True)) is not Tier.HIGH_CONVICTION

    def test_expensive_but_with_upside_is_watch(self):
        assert classify(make_row(price=400.0, target=200.0, upside=40.0)) is Tier.WATCH

    def test_no_upside_qualifies_for_nothing(self):
        assert classify(make_row(price=400.0, target=200.0, upside=2.0)) is Tier.NONE

    def test_missing_price_is_none(self):
        assert classify(make_row(price=None)) is Tier.NONE


class TestPick:
    def test_discount_measures_against_the_entry_price(self):
        # entry 2/3 of 200 is 132; at 66 that is a 50% discount.
        pick = to_pick(make_row(price=66.0, target=200.0))
        assert pick.discount_to_entry_pct == pytest.approx(50.0)

    def test_trading_above_entry_reads_negative(self):
        pick = to_pick(make_row(price=200.0, target=200.0))
        assert pick.discount_to_entry_pct < 0
        assert not pick.is_actionable

    def test_actionable_when_below_entry(self):
        assert to_pick(make_row(price=100.0, target=200.0)).is_actionable


class TestFlags:
    def test_unenriched_is_flagged(self):
        assert "not enriched" in to_pick(make_row(enriched=False)).flags

    def test_new_listing_is_flagged(self):
        assert "newly listed" in to_pick(make_row(newly_listed=True)).flags

    def test_absurd_upside_is_flagged(self):
        assert "upside implausibly large" in to_pick(make_row(upside=900.0)).flags

    def test_thin_history_is_flagged(self):
        assert "thin history" in to_pick(make_row(quarters=5)).flags

    def test_a_clean_company_has_no_flags(self):
        assert to_pick(make_row()).flags == []


class TestRank:
    def test_conviction_outranks_a_bigger_upside(self):
        # Sorting on upside alone would put the weakly corroborated name first.
        strong = make_row(buys=8, upside=30.0)
        loud = make_row(buys=2, upside=500.0)
        ranked = rank([loud, strong])
        assert ranked[0].buy_signals > ranked[-1].buy_signals
        assert ranked[0].upside_pct == 30.0

    def test_tier_filter(self):
        rows = [make_row(), make_row(buys=2)]
        assert all(p.tier is Tier.HIGH_CONVICTION
                   for p in rank(rows, tier=Tier.HIGH_CONVICTION))

    def test_limit_applies(self):
        assert len(rank([make_row() for _ in range(5)], limit=2)) == 2

    def test_unqualified_rows_are_excluded(self):
        assert rank([make_row(price=400.0, target=200.0, upside=2.0)]) == []


class TestSectorHeat:
    def test_counts_high_conviction_share(self):
        rows = [make_row(sector="Widgets") for _ in range(2)]
        rows += [make_row(sector="Widgets", price=400.0, upside=2.0) for _ in range(2)]
        heat = sector_heat(rows, min_companies=1)
        assert heat[0].sector == "Widgets"
        assert heat[0].picks == 2 and heat[0].total == 4
        assert heat[0].share_pct == pytest.approx(50.0)

    def test_small_sectors_are_ignored(self):
        assert sector_heat([make_row(sector="Tiny")], min_companies=3) == []

    def test_sectors_with_no_picks_are_omitted(self):
        rows = [make_row(sector="Cold", price=400.0, upside=2.0) for _ in range(4)]
        assert sector_heat(rows, min_companies=1) == []

    def test_ranked_by_share_not_absolute_count(self):
        rows = [make_row(sector="Small") for _ in range(3)]
        rows += [make_row(sector="Big") for _ in range(4)]
        rows += [make_row(sector="Big", price=400.0, upside=2.0) for _ in range(40)]
        assert sector_heat(rows, min_companies=3)[0].sector == "Small"


class TestRerating:
    """Sound businesses that have already moved, target still ahead.

    The case this exists for: a share rises, its P/E and price-to-book turn SELL
    *because* it rose, the headline score drops below every threshold, and the
    company vanishes from the screens while still trading under its target.
    """

    def rerating_row(self, **kw):
        # Welspun's shape: every quality signal sound, but the run has made it
        # expensive, so P/E and price-to-book read SELL. Price sits above the
        # 3/4 entry of 150 and below the 200 target, models still agreeing.
        kw = {"price": 170.0, "target": 200.0, "upside": 20.0, "buys": 8,
              "pe_yearly": 195.0, "pe_quarterly": 190.0,
              "stock_pe": 68.0, "book_value": 17.0, **kw}
        return make_row(**kw)

    def test_a_risen_but_sound_company_is_tiered(self):
        assert classify(self.rerating_row()) is Tier.RERATING

    def test_price_based_sells_do_not_disqualify_it(self):
        # buys=6 leaves P/E as a SELL: the whole point is to ignore that.
        row = self.rerating_row()
        assert row.screening.value_sell_count > 0
        assert row.screening.quality_sell_count == 0
        assert classify(row) is Tier.RERATING

    def test_a_deteriorating_business_is_excluded(self):
        # buys=3 puts genuine quality signals into SELL, not just cheapness ones.
        row = self.rerating_row(buys=3)
        assert row.screening.quality_sell_count > 0
        assert classify(row) is not Tier.RERATING

    def test_it_must_still_have_headroom(self):
        # 5% left to the target is not an opportunity, it is a finished move.
        assert classify(self.rerating_row(upside=5.0)) is not Tier.RERATING

    def test_a_company_below_its_entry_price_stays_in_the_entry_tiers(self):
        # Those tiers are stronger claims -- the margin of safety is intact --
        # so this one must not poach from them.
        assert classify(self.rerating_row(price=100.0)) is Tier.HIGH_CONVICTION
        assert classify(self.rerating_row(price=149.0)) is Tier.BELOW_ENTRY

    def test_one_lone_model_is_not_enough(self):
        row = self.rerating_row(pe_yearly=50.0, pe_quarterly=40.0)
        assert classify(row) is not Tier.RERATING

    def test_a_negative_target_cannot_qualify(self):
        assert classify(self.rerating_row(target=-50.0)) is not Tier.RERATING

    def test_financials_and_new_listings_are_excluded(self):
        assert classify(self.rerating_row(financial=True)) is not Tier.RERATING
        assert classify(self.rerating_row(newly_listed=True)) is not Tier.RERATING

    def test_it_is_not_presented_as_actionable(self):
        # It trades above the entry price, so the margin of safety is gone.
        assert not to_pick(self.rerating_row()).is_actionable

    def test_ranked_on_headroom_not_the_headline_score(self):
        near = self.rerating_row(upside=16.0)
        far = self.rerating_row(upside=90.0, target=400.0, price=320.0,
                                pe_yearly=390.0, pe_quarterly=380.0)
        ranked = rank([near, far], tier=Tier.RERATING)
        assert [p.upside_pct for p in ranked] == [90.0, 16.0]


class TestNegativeTargets:
    """A forecast of negative EBITDA is a real output, but not a price."""

    def test_a_negative_target_is_not_actionable(self):
        # Unguarded, every such company read as trading below its entry price:
        # 129 of them on real data.
        pick = to_pick(make_row(price=45.0, target=-50.0))
        assert not pick.is_actionable
        assert pick.discount_to_entry_pct is None

    def test_it_is_flagged(self):
        assert "model target is negative" in to_pick(make_row(target=-50.0)).flags

    def test_it_cannot_reach_a_tier_that_depends_on_entry_price(self):
        assert classify(make_row(price=45.0, target=-50.0)) is not Tier.HIGH_CONVICTION
        assert classify(make_row(price=45.0, target=-50.0)) is not Tier.BELOW_ENTRY

    def test_a_positive_target_is_unaffected(self):
        pick = to_pick(make_row(price=100.0, target=200.0))
        assert pick.has_usable_target
        assert pick.is_actionable


def snap(day, tickers):
    return {"taken_on": f"2026-11-{day:02d}", "tickers": set(tickers)}


class TestChurn:
    """How the shortlist moved between two publishes."""

    def test_it_counts_entries_exits_and_holds(self):
        moved = churn({"A", "B", "C"}, {"B", "C", "D"},
                      on="2026-11-11", since="2026-11-10")
        assert (moved.added, moved.exited, moved.held) == (1, 1, 2)
        assert (moved.on, moved.since) == ("2026-11-11", "2026-11-10")

    def test_an_unchanged_shortlist_reports_no_movement(self):
        moved = churn({"A", "B"}, {"A", "B"}, on="x", since="y")
        assert (moved.added, moved.exited) == (0, 0)
        assert moved.changed is False

    def test_any_movement_at_all_counts_as_changed(self):
        assert churn({"A"}, set(), on="x", since="y").changed is True
        assert churn(set(), {"A"}, on="x", since="y").changed is True

    def test_a_first_run_is_all_entries(self):
        moved = churn({"A", "B"}, set(), on="x", since="y")
        assert (moved.added, moved.exited, moved.held) == (2, 0, 0)

    def test_an_emptied_shortlist_is_all_exits(self):
        moved = churn(set(), {"A", "B"}, on="x", since="y")
        assert (moved.added, moved.exited, moved.held) == (0, 2, 0)


class TestLastChange:
    """When the page last said something different -- not when it last ran."""

    def test_it_skips_past_runs_that_changed_nothing(self):
        """The whole point of the function.

        Publishing is nightly, so the newest snapshot is always today's. Using
        that as "last changed" would report today forever and be no more useful
        than the price date already on the page.
        """
        snaps = [snap(18, "ABC"), snap(17, "ABC"), snap(16, "ABC"),
                 snap(15, "ABD")]
        moved = last_change(snaps)
        assert moved.on == "2026-11-16"
        assert (moved.added, moved.exited) == (1, 1)

    def test_a_change_on_the_newest_run_is_reported_as_today(self):
        moved = last_change([snap(18, "ABC"), snap(17, "AB")])
        assert moved.on == "2026-11-18"
        assert (moved.added, moved.exited) == (1, 0)

    def test_an_unchanged_window_is_none_not_a_zero_change(self):
        # None means "not within these runs", which the caller reports as a
        # window. A Churn of 0/0 would read as "it changed, by nothing".
        assert last_change([snap(18, "AB"), snap(17, "AB"), snap(16, "AB")]) is None

    def test_a_single_snapshot_has_nothing_to_compare(self):
        assert last_change([snap(18, "AB")]) is None

    def test_no_snapshots_is_not_an_error(self):
        assert last_change([]) is None

    def test_it_stops_at_the_first_change_rather_than_the_largest(self):
        # Walking on to find a bigger move would report an older date as the
        # last change, which is simply wrong.
        snaps = [snap(18, "ABC"), snap(17, "ABD"), snap(16, "XYZ")]
        assert last_change(snaps).on == "2026-11-18"
