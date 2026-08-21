"""Alert rules.

The property that matters is that an alert fires on a *transition*. A rule that
matched on state alone would resend the same six hundred companies every night
until the reader muted it, which is the failure mode that makes alerting
worthless.
"""

from __future__ import annotations

import pytest

from mcfinex.alerts import Rule, Snapshot, Trigger, evaluate
from mcfinex.picks import Tier
from mcfinex.report import Row
from mcfinex.screening import Metrics, Verdict, screen


def row(ticker="ACME", *, price=100.0, target=200.0, upside=60.0, buys=7,
        roce=25.0, newly_listed=False):
    metrics = Metrics(
        ticker=ticker, name=f"{ticker} Ltd", sector="Widgets", price=price,
        ev_ebitda_upside=upside, quarters_reported=0 if newly_listed else 12,
        current_assets=150.0, current_liabilities=100.0,
        roce=roce, dividend_yield=3.0, free_cash_flow=10.0,
        reserves=900.0, equity_capital=100.0, book_value=200.0,
        promoter_holding=70.0, stock_pe=10.0, sector_pe=20.0,
        inventory_days=80.0, inventory_days_prior=100.0,
    )
    return Row(
        screening=screen(metrics), metrics=metrics, target_ev_ebitda=target,
        target_pe_yearly=target * 0.9, target_pe_quarterly=target * 0.85,
        entry_3by4=target * 0.75 if target and target > 0 else None,
        entry_2by3=target * 0.66 if target and target > 0 else None,
    )


def snapshot_of(rows, rules=()):
    _, state = evaluate(rows, rules, {})
    return state


class TestTransitions:
    def test_nothing_fires_when_nothing_changed(self):
        rules = [Rule(Trigger.TIER_ENTERED, tier=Tier.HIGH_CONVICTION),
                 Rule(Trigger.ENTRY_REACHED)]
        rows = [row()]
        state = snapshot_of(rows, rules)
        alerts, _ = evaluate(rows, rules, state)
        assert alerts == []

    def test_entering_a_tier_fires_once(self):
        rules = [Rule(Trigger.TIER_ENTERED, tier=Tier.HIGH_CONVICTION)]
        before = snapshot_of([row(price=400.0)], rules)     # too expensive to qualify
        alerts, state = evaluate([row(price=100.0)], rules, before)
        assert [a.trigger for a in alerts] == [Trigger.TIER_ENTERED]
        # And not again on the next run.
        assert evaluate([row(price=100.0)], rules, state)[0] == []

    def test_leaving_a_tier_fires(self):
        rules = [Rule(Trigger.TIER_LEFT, tier=Tier.HIGH_CONVICTION)]
        before = snapshot_of([row(price=100.0)], rules)
        alerts, _ = evaluate([row(price=400.0)], rules, before)
        assert [a.trigger for a in alerts] == [Trigger.TIER_LEFT]

    def test_a_company_seen_for_the_first_time_is_not_a_transition(self):
        # Otherwise the first run after adding the universe alerts on everything.
        rules = [Rule(Trigger.TIER_ENTERED, tier=Tier.HIGH_CONVICTION)]
        alerts, _ = evaluate([row()], rules, {})
        assert alerts == []


class TestEntryReached:
    def test_fires_when_price_falls_below_the_entry(self):
        rules = [Rule(Trigger.ENTRY_REACHED)]
        before = snapshot_of([row(price=400.0)], rules)
        alerts, _ = evaluate([row(price=100.0)], rules, before)
        assert [a.trigger for a in alerts] == [Trigger.ENTRY_REACHED]

    def test_does_not_fire_while_it_stays_below(self):
        rules = [Rule(Trigger.ENTRY_REACHED)]
        state = snapshot_of([row(price=100.0)], rules)
        assert evaluate([row(price=90.0)], rules, state)[0] == []

    def test_a_negative_target_never_counts_as_reached(self):
        # A negative target made every price look like a discount: 129 companies
        # read as actionable on real data.
        rules = [Rule(Trigger.ENTRY_REACHED)]
        before = snapshot_of([row(price=400.0, target=-50.0)], rules)
        alerts, _ = evaluate([row(price=45.0, target=-50.0)], rules, before)
        assert alerts == []


class TestSignalChanged:
    def test_fires_on_the_named_signal(self):
        rules = [Rule(Trigger.SIGNAL_CHANGED, signal="ROCE %")]
        before = snapshot_of([row(roce=25.0)], rules)
        alerts, _ = evaluate([row(roce=2.0)], rules, before)
        assert [a.trigger for a in alerts] == [Trigger.SIGNAL_CHANGED]
        assert "ROCE %" in alerts[0].headline

    def test_can_be_narrowed_to_one_destination_verdict(self):
        rules = [Rule(Trigger.SIGNAL_CHANGED, signal="ROCE %", to_verdict=Verdict.BUY)]
        before = snapshot_of([row(roce=25.0)], rules)
        assert evaluate([row(roce=2.0)], rules, before)[0] == []   # became SELL

    def test_other_signals_are_ignored(self):
        rules = [Rule(Trigger.SIGNAL_CHANGED, signal="Dividend yield %")]
        before = snapshot_of([row(roce=25.0)], rules)
        assert evaluate([row(roce=2.0)], rules, before)[0] == []


class TestUpsideCrossed:
    def test_fires_when_it_crosses_upward(self):
        rules = [Rule(Trigger.UPSIDE_CROSSED, threshold=50.0)]
        before = snapshot_of([row(upside=20.0)], rules)
        alerts, _ = evaluate([row(upside=60.0)], rules, before)
        assert "above 50" in alerts[0].headline

    def test_fires_when_it_crosses_downward(self):
        rules = [Rule(Trigger.UPSIDE_CROSSED, threshold=50.0)]
        before = snapshot_of([row(upside=60.0)], rules)
        alerts, _ = evaluate([row(upside=20.0)], rules, before)
        assert "below 50" in alerts[0].headline

    def test_movement_on_the_same_side_does_not_fire(self):
        rules = [Rule(Trigger.UPSIDE_CROSSED, threshold=50.0)]
        before = snapshot_of([row(upside=60.0)], rules)
        assert evaluate([row(upside=80.0)], rules, before)[0] == []


class TestWatchlistAndLimits:
    def test_a_watchlist_narrows_the_rule(self):
        rules = [Rule(Trigger.ENTRY_REACHED, watchlist=frozenset({"AAA"}))]
        before = snapshot_of([row("AAA", price=400.0), row("BBB", price=400.0)], rules)
        alerts, _ = evaluate([row("AAA", price=100.0), row("BBB", price=100.0)],
                             rules, before)
        assert [a.ticker for a in alerts] == ["AAA"]

    def test_limit_caps_one_rule(self):
        rules = [Rule(Trigger.ENTRY_REACHED, limit=2)]
        expensive = [row(f"T{i}", price=400.0) for i in range(5)]
        cheap = [row(f"T{i}", price=100.0) for i in range(5)]
        before = snapshot_of(expensive, rules)
        assert len(evaluate(cheap, rules, before)[0]) == 2


class TestDailyPick:
    def test_fires_without_a_transition(self):
        # The point of a daily pick is that it arrives daily.
        rules = [Rule(Trigger.DAILY_PICK, limit=1)]
        rows = [row()]
        state = snapshot_of(rows, rules)
        assert len(evaluate(rows, rules, state)[0]) == 1

    def test_skips_companies_carrying_caveats(self):
        rules = [Rule(Trigger.DAILY_PICK, limit=1)]
        assert evaluate([row(newly_listed=True)], rules, {})[0] == []


class TestState:
    def test_state_is_returned_not_written(self):
        # A caller whose delivery failed can decline to save it, so the same
        # alerts fire again rather than being silently swallowed.
        rules = [Rule(Trigger.ENTRY_REACHED)]
        alerts, state = evaluate([row()], rules, {})
        assert isinstance(state, dict) and "ACME" in state

    def test_snapshot_round_trips_through_json(self):
        snap = Snapshot(tier="Watch", actionable=True, upside_pct=12.5,
                        verdicts={"ROCE %": "BUY"})
        assert Snapshot.from_dict(snap.as_dict()) == snap

    def test_alerts_are_ordered_by_weight(self):
        rules = [Rule(Trigger.ENTRY_REACHED), Rule(Trigger.UPSIDE_CROSSED, threshold=50.0)]
        before = snapshot_of([row(price=400.0, upside=20.0)], rules)
        alerts, _ = evaluate([row(price=100.0, upside=60.0)], rules, before)
        assert [a.weight for a in alerts] == sorted((a.weight for a in alerts), reverse=True)
