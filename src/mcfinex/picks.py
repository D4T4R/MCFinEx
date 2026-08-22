"""Selecting and ranking the landing page's candidates.

Raw upside does not discriminate: 74% of the scraped universe shows more than
10% EV/EBITDA upside, because the model projects historical EBITDA growth and
most companies have some. A list of 1,876 "cheap" stocks is not a shortlist.

So a pick has to clear the workbook's own discipline -- trade below its entry
price, the deliberate margin of safety under the target -- and be corroborated:
enough fundamental signals, and all three valuation models pointing the same
way. That lands around 130 names rather than 1,900.

Pure: no database, no UI, no I/O. Takes the rows :mod:`mcfinex.report` builds.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

#: Fundamental BUY signals a pick must carry.
MIN_BUY_SIGNALS = 6
#: Upside below which a name is not worth surfacing at all.
WATCH_UPSIDE_PCT = 25.0
#: Above this, the target is more likely a data artefact than an opportunity.
IMPLAUSIBLE_UPSIDE_PCT = 300.0

#: Quality signals a re-rating name must still carry. Four of the seven, with
#: none of them negative -- the entry tiers ask for more, but those names are
#: cheap and this one deliberately is not.
RERATING_MIN_QUALITY_BUYS = 4
#: Headroom left to the target. Below this the run is essentially over.
RERATING_MIN_UPSIDE_PCT = 15.0


class Tier(str, Enum):
    #: Below the 2/3 entry price, well corroborated.
    HIGH_CONVICTION = "High conviction"
    #: Below an entry price, but less corroborated.
    BELOW_ENTRY = "Below entry price"
    #: Already run past its entry price, but the target is not reached and the
    #: business still screens well.
    RERATING = "Re-rating"
    #: Cheap on the headline model only.
    WATCH = "Watch"
    #: Did not qualify.
    NONE = "None"


@dataclass
class Pick:
    ticker: str
    name: str | None
    sector: str | None
    price: float | None
    target: float | None
    entry_3by4: float | None
    entry_2by3: float | None
    upside_pct: float | None
    buy_signals: int
    sell_signals: int
    scored: int
    models_agreeing: int
    tier: Tier
    flags: list[str]
    #: The score with the price-based signals taken out, so a share that rose is
    #: not marked down for having risen.
    quality_buys: int = 0
    quality_sells: int = 0

    @property
    def has_usable_target(self) -> bool:
        """Whether the model produced a price anyone could act on.

        A company whose EBITDA is forecast to go negative gets a negative
        enterprise value and so a negative target. That is a real output --
        the model is saying the operations are worth nothing -- but it is not
        a price. Left unguarded, every such company reads as trading below its
        entry price, which was true of 129 of them.
        """
        return self.target is not None and self.target > 0 and (self.entry_2by3 or 0) > 0

    @property
    def discount_to_entry_pct(self) -> float | None:
        """How far below the 2/3 entry price it trades. Negative means above it.

        This is the actionable number: the target says what the model thinks it
        is worth, the entry price says where it becomes worth acting on.
        """
        if self.price is None or not self.has_usable_target:
            return None
        return (self.entry_2by3 - self.price) / self.entry_2by3 * 100

    @property
    def is_actionable(self) -> bool:
        gap = self.discount_to_entry_pct
        return gap is not None and gap > 0


def _models_agreeing(row) -> int:
    price = row.metrics.price
    if not price:
        return 0
    targets = (row.target_ev_ebitda, row.target_pe_yearly, row.target_pe_quarterly)
    return sum(1 for t in targets if t and t > price)


def _flags(row) -> list[str]:
    """Caveats that should travel with the number, not be discovered later."""
    flags: list[str] = []
    m = row.metrics
    if m.newly_listed:
        flags.append("newly listed")
    if m.is_financial:
        flags.append("financial: EV/EBITDA withheld")
    if m.current_assets is None:
        flags.append("not enriched")
    if m.quarters_reported is not None and 0 < m.quarters_reported < 8:
        flags.append("thin history")
    if (m.ev_ebitda_upside or 0) > IMPLAUSIBLE_UPSIDE_PCT:
        flags.append("upside implausibly large")
    if (row.target_ev_ebitda or 0) <= 0:
        flags.append("model target is negative")
    return flags


def classify(row) -> Tier:
    """Which tier a screened row belongs to."""
    m, s = row.metrics, row.screening
    price, upside = m.price, m.ev_ebitda_upside
    if not price or upside is None:
        return Tier.NONE
    # A new listing's per-share history spans its IPO and a bank has no
    # meaningful EV/EBITDA, so neither can be corroborated the usual way.
    disqualified = m.newly_listed or m.is_financial

    # A negative target makes every price look like a discount, so the entry
    # tests only apply where the model produced a usable one.
    usable = (row.target_ev_ebitda or 0) > 0
    below_2by3 = bool(usable and row.entry_2by3 and price < row.entry_2by3)
    below_3by4 = bool(usable and row.entry_3by4 and price < row.entry_3by4)

    if (below_2by3 and not disqualified
            and s.buy_count >= MIN_BUY_SIGNALS
            and _models_agreeing(row) == 3):
        return Tier.HIGH_CONVICTION
    if below_3by4 and not disqualified:
        return Tier.BELOW_ENTRY
    if _is_rerating(row, usable=usable, disqualified=disqualified):
        return Tier.RERATING
    if upside > WATCH_UPSIDE_PCT:
        return Tier.WATCH
    return Tier.NONE


def _is_rerating(row, *, usable: bool, disqualified: bool) -> bool:
    """A sound business that has already moved, with the target still ahead.

    The headline score misses these. It counts all ten fundamentals equally, so
    a share that has risen loses its P/E, price-to-book and dividend-yield
    signals *because* it rose -- three of the ten flip to SELL on the strength
    of the move itself, and the company drops out of every tier that asks for
    six BUYs. Welspun Corp is the case that prompted this: seven of nine scored
    signals fine, both SELLs are cheapness measures, and it still sits below the
    EV/EBITDA target.

    So the test ignores the price-based signals and asks only whether the
    business still screens well, then requires real headroom left and more than
    one model saying so. Deliberately not a buy: these names are past the margin
    of safety the entry tiers insist on, which is why they are tiered apart
    rather than mixed into the picks.
    """
    m, s = row.metrics, row.screening
    if disqualified or not usable or not row.entry_3by4:
        return False
    # Past the entry price is the point -- below it, the existing tiers own it.
    if m.price <= row.entry_3by4:
        return False
    return (
        s.quality_buy_count >= RERATING_MIN_QUALITY_BUYS
        and s.quality_sell_count == 0
        and (m.ev_ebitda_upside or 0) >= RERATING_MIN_UPSIDE_PCT
        and (m.ev_ebitda_upside or 0) <= IMPLAUSIBLE_UPSIDE_PCT
        and _models_agreeing(row) >= 2
    )


def to_pick(row) -> Pick:
    m, s = row.metrics, row.screening
    return Pick(
        ticker=s.ticker, name=s.name, sector=m.sector, price=m.price,
        target=row.target_ev_ebitda, entry_3by4=row.entry_3by4,
        entry_2by3=row.entry_2by3, upside_pct=m.ev_ebitda_upside,
        buy_signals=s.buy_count, sell_signals=s.sell_count, scored=s.scored_count,
        models_agreeing=_models_agreeing(row), tier=classify(row), flags=_flags(row),
        quality_buys=s.quality_buy_count, quality_sells=s.quality_sell_count,
    )


def rank(rows, tier: Tier | None = None, limit: int | None = None) -> list[Pick]:
    """Picks, best first. Ranked by corroboration before size of upside.

    Conviction leads deliberately: a 400% upside on one model with two BUY
    signals is noise, and sorting on upside alone would put it top.
    """
    picks = [to_pick(r) for r in rows]
    if tier is not None:
        picks = [p for p in picks if p.tier is tier]
    else:
        picks = [p for p in picks if p.tier is not Tier.NONE]
    picks.sort(key=_sort_key)
    return picks[:limit] if limit else picks


#: Strength of claim, strongest first. Ranking across tiers groups by this so a
#: weakly-corroborated name can never lead a better-corroborated one.
_TIER_ORDER = {
    Tier.HIGH_CONVICTION: 0, Tier.BELOW_ENTRY: 1,
    Tier.RERATING: 2, Tier.WATCH: 3, Tier.NONE: 4,
}


def _sort_key(pick: Pick) -> tuple:
    """Order within a tier on the terms that tier is actually about.

    Re-rating names need their own ordering, and the key is tied to the pick
    rather than to the filter argument because callers rank the whole universe
    once and filter afterwards -- a key chosen from the argument would simply
    never apply on that path.
    """
    if pick.tier is Tier.RERATING:
        # Their discount is negative by construction and their price-based
        # signals are SELL, so the headline key would rank them by the very
        # bias that hid them. Headroom to the target is what means something.
        within = (-pick.quality_buys, -(pick.upside_pct or 0),
                  -pick.models_agreeing, 0.0)
    else:
        gap = pick.discount_to_entry_pct
        within = (-pick.buy_signals, -pick.models_agreeing,
                  -(gap if gap is not None else -999), float(pick.sell_signals))
    return (_TIER_ORDER[pick.tier], *within)


@dataclass
class SectorHeat:
    sector: str
    picks: int
    total: int
    median_upside_pct: float | None

    @property
    def share_pct(self) -> float:
        return self.picks / self.total * 100 if self.total else 0.0


def sector_heat(rows, *, min_companies: int = 3, limit: int | None = None) -> list[SectorHeat]:
    """Where conviction is clustering, rather than one-off outliers.

    Counts high-conviction names only. Including everything below an entry price
    would count 45% of the universe, so a sector at 90% would sit barely above
    the base rate and tell you nothing. High conviction is ~5% of the market, so
    a sector well above that is genuinely unusual.

    Ranked by the share of a sector that qualifies, so a small sector with three
    of five names cheap outranks a large one with a handful.
    """
    import statistics

    buckets: dict[str, list] = {}
    for row in rows:
        sector = row.metrics.sector
        if sector:
            buckets.setdefault(sector, []).append(row)

    heat: list[SectorHeat] = []
    for sector, group in buckets.items():
        if len(group) < min_companies:
            continue
        qualifying = [r for r in group if classify(r) is Tier.HIGH_CONVICTION]
        if not qualifying:
            continue
        upsides = [r.metrics.ev_ebitda_upside for r in qualifying
                   if r.metrics.ev_ebitda_upside is not None]
        heat.append(SectorHeat(
            sector=sector, picks=len(qualifying), total=len(group),
            median_upside_pct=statistics.median(upsides) if upsides else None,
        ))
    heat.sort(key=lambda h: (-h.share_pct, -h.picks))
    return heat[:limit] if limit else heat
