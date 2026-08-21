"""Alert rules: what to watch for, and what changed since last time.

An alert is only worth sending when something *became* true. A rule that simply
matches "upside above 40%" would fire for the same six hundred companies every
night until the reader muted it, so every rule is evaluated against the previous
run and only transitions are reported.

Pure by design: no database, no network, no push. It takes the screened rows and
the previous state, and returns what to say. Delivery is somebody else's job,
which is what lets the same rules serve a phone, a webhook and an email digest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable

from .picks import Pick, Tier, to_pick
from .screening import Verdict


class Trigger(str, Enum):
    #: Entered a tier it was not in before.
    TIER_ENTERED = "tier_entered"
    #: Fell out of a tier.
    TIER_LEFT = "tier_left"
    #: Price crossed below an entry price, so the model calls it actionable.
    ENTRY_REACHED = "entry_reached"
    #: A named signal changed its verdict.
    SIGNAL_CHANGED = "signal_changed"
    #: Upside crossed a threshold in either direction.
    UPSIDE_CROSSED = "upside_crossed"
    #: Highest-conviction name of the day, sent whether or not it changed.
    DAILY_PICK = "daily_pick"


@dataclass(frozen=True)
class Alert:
    ticker: str
    name: str | None
    trigger: Trigger
    #: One line, already written for a notification body.
    headline: str
    detail: str = ""
    #: Higher sorts first when a run produces more than a phone should show.
    weight: int = 0

    def as_dict(self) -> dict:
        return {
            "ticker": self.ticker, "name": self.name, "trigger": self.trigger.value,
            "headline": self.headline, "detail": self.detail, "weight": self.weight,
        }


@dataclass
class Snapshot:
    """What a company looked like on the previous run.

    Only the fields a rule compares, so the stored state stays small and a
    schema change to Metrics does not invalidate every reader's history.
    """

    tier: str | None = None
    actionable: bool = False
    upside_pct: float | None = None
    verdicts: dict[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, pick: Pick, verdicts: dict[str, str]) -> "Snapshot":
        return cls(
            tier=pick.tier.value,
            actionable=pick.is_actionable,
            upside_pct=pick.upside_pct,
            verdicts=verdicts,
        )

    def as_dict(self) -> dict:
        return {
            "tier": self.tier, "actionable": self.actionable,
            "upside_pct": self.upside_pct, "verdicts": self.verdicts,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Snapshot":
        return cls(
            tier=raw.get("tier"),
            actionable=bool(raw.get("actionable")),
            upside_pct=raw.get("upside_pct"),
            verdicts=raw.get("verdicts") or {},
        )


@dataclass
class Rule:
    """One thing a reader asked to be told about."""

    trigger: Trigger
    #: For TIER_ENTERED / TIER_LEFT.
    tier: Tier | None = None
    #: For SIGNAL_CHANGED: the signal label, and which verdicts count.
    signal: str | None = None
    to_verdict: Verdict | None = None
    #: For UPSIDE_CROSSED.
    threshold: float | None = None
    #: Only consider these tickers. Empty means the whole universe.
    watchlist: frozenset[str] = frozenset()
    #: Never send more than this many from one rule in one run.
    limit: int = 10

    def covers(self, ticker: str) -> bool:
        return not self.watchlist or ticker in self.watchlist


def evaluate(rows: Iterable, rules: Iterable[Rule],
             previous: dict[str, Snapshot]) -> tuple[list[Alert], dict[str, Snapshot]]:
    """Apply every rule, and return the alerts plus the state to store.

    The new state is returned rather than written, so a caller that fails to
    deliver can decline to save it and the same alerts fire again next run.
    Silently advancing the state on a failed send is how alerts go missing.
    """
    picks: dict[str, Pick] = {}
    verdicts: dict[str, dict[str, str]] = {}
    names: dict[str, str | None] = {}
    for row in rows:
        pick = to_pick(row)
        picks[pick.ticker] = pick
        verdicts[pick.ticker] = {s.label: s.verdict.value for s in row.screening.signals}
        names[pick.ticker] = pick.name

    alerts: list[Alert] = []
    for rule in rules:
        alerts.extend(_apply(rule, picks, verdicts, previous))

    state = {t: Snapshot.of(p, verdicts[t]) for t, p in picks.items()}
    alerts.sort(key=lambda a: (-a.weight, a.ticker))
    return alerts, state


def _apply(rule: Rule, picks: dict[str, Pick], verdicts: dict[str, dict[str, str]],
           previous: dict[str, Snapshot]) -> list[Alert]:
    handler: Callable = _HANDLERS[rule.trigger]
    found: list[Alert] = []
    for ticker, pick in picks.items():
        if not rule.covers(ticker):
            continue
        alert = handler(rule, pick, verdicts.get(ticker, {}), previous.get(ticker))
        if alert is not None:
            found.append(alert)
    found.sort(key=lambda a: -a.weight)
    return found[: rule.limit]


def _tier_entered(rule: Rule, pick: Pick, _verdicts, before: Snapshot | None) -> Alert | None:
    wanted = (rule.tier or Tier.HIGH_CONVICTION).value
    if pick.tier.value != wanted:
        return None
    # No history means a company we have not seen before, not a transition.
    if before is None or before.tier == wanted:
        return None
    return Alert(
        pick.ticker, pick.name, Trigger.TIER_ENTERED,
        f"{pick.ticker} entered {wanted}",
        _describe(pick), weight=30 if wanted == Tier.HIGH_CONVICTION.value else 15,
    )


def _tier_left(rule: Rule, pick: Pick, _verdicts, before: Snapshot | None) -> Alert | None:
    wanted = (rule.tier or Tier.HIGH_CONVICTION).value
    if before is None or before.tier != wanted or pick.tier.value == wanted:
        return None
    return Alert(
        pick.ticker, pick.name, Trigger.TIER_LEFT,
        f"{pick.ticker} left {wanted}",
        f"Now {pick.tier.value}. {_describe(pick)}", weight=20,
    )


def _entry_reached(_rule: Rule, pick: Pick, _verdicts, before: Snapshot | None) -> Alert | None:
    if not pick.is_actionable or (before is not None and before.actionable):
        return None
    gap = pick.discount_to_entry_pct
    return Alert(
        pick.ticker, pick.name, Trigger.ENTRY_REACHED,
        f"{pick.ticker} reached its entry price",
        f"{pick.price:,.2f} is {gap:.0f}% below the 2/3 entry of {pick.entry_2by3:,.2f}."
        if gap is not None and pick.price and pick.entry_2by3 else _describe(pick),
        weight=40,
    )


def _signal_changed(rule: Rule, pick: Pick, verdicts: dict[str, str],
                    before: Snapshot | None) -> Alert | None:
    if before is None or not rule.signal:
        return None
    was, now = before.verdicts.get(rule.signal), verdicts.get(rule.signal)
    if now is None or was is None or was == now:
        return None
    if rule.to_verdict is not None and now != rule.to_verdict.value:
        return None
    return Alert(
        pick.ticker, pick.name, Trigger.SIGNAL_CHANGED,
        f"{pick.ticker}: {rule.signal} {was} to {now}",
        _describe(pick), weight=25 if now == Verdict.SELL.value else 20,
    )


def _upside_crossed(rule: Rule, pick: Pick, _verdicts, before: Snapshot | None) -> Alert | None:
    threshold = rule.threshold
    if threshold is None or before is None:
        return None
    was, now = before.upside_pct, pick.upside_pct
    if was is None or now is None or (was >= threshold) == (now >= threshold):
        return None
    direction = "above" if now >= threshold else "below"
    return Alert(
        pick.ticker, pick.name, Trigger.UPSIDE_CROSSED,
        f"{pick.ticker} upside crossed {direction} {threshold:.0f}%",
        f"Now {now:+.1f}%, was {was:+.1f}%.", weight=18,
    )


def _daily_pick(_rule: Rule, pick: Pick, _verdicts, _before) -> Alert | None:
    # Ranked and trimmed by the caller; every candidate is returned so the
    # rule's limit picks the best.
    if pick.tier is not Tier.HIGH_CONVICTION or pick.flags:
        return None
    return Alert(
        pick.ticker, pick.name, Trigger.DAILY_PICK,
        f"Today's pick: {pick.ticker}", _describe(pick),
        weight=pick.buy_signals * 10 + int(pick.discount_to_entry_pct or 0),
    )


_HANDLERS: dict[Trigger, Callable] = {
    Trigger.TIER_ENTERED: _tier_entered,
    Trigger.TIER_LEFT: _tier_left,
    Trigger.ENTRY_REACHED: _entry_reached,
    Trigger.SIGNAL_CHANGED: _signal_changed,
    Trigger.UPSIDE_CROSSED: _upside_crossed,
    Trigger.DAILY_PICK: _daily_pick,
}


def _describe(pick: Pick) -> str:
    bits = []
    if pick.price:
        bits.append(f"{pick.price:,.2f}")
    if pick.upside_pct is not None:
        bits.append(f"{pick.upside_pct:+.0f}% upside")
    bits.append(f"{pick.buy_signals}/{pick.scored} BUY")
    if pick.flags:
        bits.append(f"({pick.flags[0]})")
    return " · ".join(bits)
