import pytest

from mcfinex.picks import Tier, classify, rank, sector_heat, to_pick
from mcfinex.report import Row
from mcfinex.screening import Metrics, screen


def make_row(*, price=100.0, target=200.0, upside=60.0, buys=7, sells=0,
             sector="Widgets", newly_listed=False, financial=False,
             pe_yearly=180.0, pe_quarterly=170.0, enriched=True, quarters=12):
    """A screened row with the levers the tiers depend on."""
    metrics = Metrics(
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
