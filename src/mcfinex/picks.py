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


class Tier(str, Enum):
    #: Below the 2/3 entry price, well corroborated.
    HIGH_CONVICTION = "High conviction"
    #: Below an entry price, but less corroborated.
    BELOW_ENTRY = "Below entry price"
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

    @property
    def discount_to_entry_pct(self) -> float | None:
        """How far below the 2/3 entry price it trades. Negative means above it.

        This is the actionable number: the target says what the model thinks it
        is worth, the entry price says where it becomes worth acting on.
        """
        if self.price is None or not self.entry_2by3:
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

    below_2by3 = bool(row.entry_2by3 and price < row.entry_2by3)
    below_3by4 = bool(row.entry_3by4 and price < row.entry_3by4)

    if (below_2by3 and not disqualified
            and s.buy_count >= MIN_BUY_SIGNALS
            and _models_agreeing(row) == 3):
        return Tier.HIGH_CONVICTION
    if below_3by4 and not disqualified:
        return Tier.BELOW_ENTRY
    if upside > WATCH_UPSIDE_PCT:
        return Tier.WATCH
    return Tier.NONE


def to_pick(row) -> Pick:
    m, s = row.metrics, row.screening
    return Pick(
        ticker=s.ticker, name=s.name, sector=m.sector, price=m.price,
        target=row.target_ev_ebitda, entry_3by4=row.entry_3by4,
        entry_2by3=row.entry_2by3, upside_pct=m.ev_ebitda_upside,
        buy_signals=s.buy_count, sell_signals=s.sell_count, scored=s.scored_count,
        models_agreeing=_models_agreeing(row), tier=classify(row), flags=_flags(row),
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
    picks.sort(
        key=lambda p: (
            -p.buy_signals,
            -p.models_agreeing,
            -(p.discount_to_entry_pct if p.discount_to_entry_pct is not None else -999),
            p.sell_signals,
        )
    )
    return picks[:limit] if limit else picks


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
