"""Landing page: the shortlist, as cards.

Deliberately not the full table. 74% of the universe shows some upside, so the
job here is to narrow rather than display -- a handful of names that clear the
entry-price discipline and are corroborated by the fundamentals. The full table
and per-company search live on the Detailed screen page.

Reads the screening layer directly rather than over HTTP, so this runs as one
process. The same functions are exposed by :mod:`mcfinex.api` for a React front
end, which is why none of the logic lives in this file.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

# Absolute imports: streamlit runs this as __main__, not as a package module.
from mcfinex import picks as picks_module
from mcfinex.config import Settings, database_ready as _database_ready
from mcfinex.disclaimer import FULL as _FULL_DISCLAIMER, SHORT as _SHORT_DISCLAIMER
from mcfinex.db.store import Store
from mcfinex.picks import Tier
from mcfinex.report import screen_all

TIER_HELP = {
    Tier.HIGH_CONVICTION: (
        "Below the 2/3 entry price, six or more fundamental BUY signals, and all "
        "three valuation models agreeing. Excludes new listings and financials."
    ),
    Tier.BELOW_ENTRY: "Below the 3/4 entry price, but less corroborated.",
    Tier.RERATING: (
        "Already run past the entry price, with the EV/EBITDA target still "
        "ahead. Four or more of the seven business-quality signals BUY and none "
        "of them SELL — P/E and price-to-book are ignored here, because a share "
        "that has risen fails those for having risen. Not a margin-of-safety "
        "buy: the discount is gone, only the headroom is left."
    ),
    Tier.WATCH: "Cheap on the EV/EBITDA model alone. Least corroborated.",
}


@st.cache_data(show_spinner="Screening...")
def load(db_path: str, revision: str):
    """Screen once per database revision; the token busts the cache on new data."""
    with Store(db_path) as store:
        rows = screen_all(store)
    # rank(), not to_pick(): store order is alphabetical, and the whole point
    # is to lead with the best corroborated rather than whatever starts with A.
    return (
        picks_module.rank(rows),
        picks_module.sector_heat(rows, limit=12),
        len(rows),
    )


def _cache_key(settings) -> str:
    """A token that changes when the underlying data does.

    Asked of the database rather than the filesystem, so a push to the hosted
    database invalidates the cache without anyone remembering to press refresh.
    """
    with Store(str(settings.db_path)) as store:
        return store.revision()


def main() -> None:
    settings = Settings.from_env()
    if not _database_ready(settings):
        st.error(f"No database at {settings.db_path}. Run `mcfinex init` then `mcfinex scrape`.")
        return

    all_picks, heat, universe = load(str(settings.db_path), _cache_key(settings))
    st.title("Ideas")
    st.caption(
        "Ranked by corroboration, not by size of upside. Screened from stored data — "
        "run `mcfinex prices` to refresh, then reload."
    )
    # Above the signals, not buried under them: a reader who scrolls straight to
    # the cards should still have met the caveat.
    st.warning(_SHORT_DISCLAIMER, icon=":material/info:")

    _headline(all_picks, universe)
    tier = _controls()
    shortlist = [p for p in all_picks if p.tier is tier]
    st.caption(TIER_HELP[tier])

    sector = _sector_filter(shortlist)
    if sector:
        shortlist = [p for p in shortlist if p.sector == sector]

    if not shortlist:
        st.info("Nothing qualifies at this tier right now.")
    else:
        _cards(shortlist[:36])
        if len(shortlist) > 36:
            st.caption(f"Showing 36 of {len(shortlist)}. Narrow by sector, or use the detailed screen.")

    _sector_heat(heat)
    st.divider()
    with st.expander("Disclaimer — please read", expanded=False):
        st.markdown(_FULL_DISCLAIMER)


def _headline(all_picks, universe: int) -> None:
    counts = {t: sum(1 for p in all_picks if p.tier is t) for t in Tier}
    a, b, c, d, e = st.columns(5)
    a.metric("Universe", f"{universe:,}")
    b.metric("High conviction", counts[Tier.HIGH_CONVICTION])
    c.metric("Below entry price", counts[Tier.BELOW_ENTRY])
    d.metric("Re-rating", counts[Tier.RERATING],
             help="Sound businesses already past their entry price, target still ahead")
    clean = sum(1 for p in all_picks if p.tier is Tier.HIGH_CONVICTION and not p.flags)
    e.metric("Without caveats", clean,
             help="High conviction and carrying no data-quality flags")


def _controls() -> Tier:
    labels = [Tier.HIGH_CONVICTION.value, Tier.BELOW_ENTRY.value,
              Tier.RERATING.value, Tier.WATCH.value]
    chosen = st.radio("Tier", labels, horizontal=True, label_visibility="collapsed")
    return Tier(chosen)


def _sector_filter(shortlist) -> str | None:
    sectors = sorted({p.sector for p in shortlist if p.sector})
    if not sectors:
        return None
    choice = st.selectbox("Sector", ["All sectors", *sectors], label_visibility="collapsed")
    return None if choice == "All sectors" else choice


def _cards(shortlist) -> None:
    for start in range(0, len(shortlist), 3):
        for column, pick in zip(st.columns(3), shortlist[start:start + 3]):
            with column:
                _card(pick)


def _card(pick) -> None:
    with st.container(border=True):
        st.markdown(f"**{pick.ticker}** · {pick.sector or '—'}")
        st.caption((pick.name or "")[:44])

        rerating = pick.tier is Tier.RERATING
        left, right = st.columns(2)
        left.metric("Price", f"{pick.price:,.2f}" if pick.price else "—")
        if rerating:
            # The discount is gone by definition, so reporting it would only
            # ever show a negative number. What is left is the distance to the
            # target, which is the reason the name is on this tier at all.
            right.metric(
                "Headroom",
                f"{pick.upside_pct:+.0f}%" if pick.upside_pct is not None else "—",
                help="How far below the EV/EBITDA target it still trades.",
            )
        else:
            gap = pick.discount_to_entry_pct
            right.metric(
                "vs entry",
                f"{gap:+.0f}%" if gap is not None else "—",
                help="How far below its 2/3 entry price it trades. Positive means it is there now.",
            )

        target = f"{pick.target:,.1f}" if pick.target else "—"
        if rerating:
            entry = f"{pick.entry_3by4:,.1f}" if pick.entry_3by4 else "—"
            st.write(f"Target **{target}** · entry **{entry}** passed")
            st.write(
                f"{pick.quality_buys}/7 quality BUY · {pick.models_agreeing}/3 models agree"
                + (f" · {pick.sell_signals} SELL on price" if pick.sell_signals else "")
            )
        else:
            entry = f"{pick.entry_2by3:,.1f}" if pick.entry_2by3 else "—"
            st.write(f"Target **{target}** · entry **{entry}**")
            st.write(
                f"{pick.buy_signals}/{pick.scored} BUY · {pick.models_agreeing}/3 models agree"
                + (f" · {pick.sell_signals} SELL" if pick.sell_signals else "")
            )
        for flag in pick.flags:
            st.caption(f":orange[⚠ {flag}]")


def _sector_heat(heat) -> None:
    if not heat:
        return
    st.subheader("Where conviction is clustering")
    st.caption(
        "Share of each sector that reaches high conviction. About 5% of the "
        "whole market does, so anything well above that is unusual."
    )
    st.dataframe(
        pd.DataFrame([
            {
                "Sector": h.sector,
                "Picks": h.picks,
                "In sector": h.total,
                "Share %": round(h.share_pct, 1),
                "Median upside %": round(h.median_upside_pct, 1) if h.median_upside_pct else None,
            }
            for h in heat
        ]),
        width="stretch", hide_index=True,
    )


main()
