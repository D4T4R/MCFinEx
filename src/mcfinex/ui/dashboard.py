"""Streamlit screener.

Replaces the workbook's Results sheet. Presentation only -- every threshold and
verdict comes from :mod:`mcfinex.screening`, which imports nothing from here, so
this can be swapped for a different front end without touching the logic.

Run with ``mcfinex-dashboard`` or ``streamlit run src/mcfinex/ui/dashboard.py``.
"""

from __future__ import annotations

import pandas as pd
import requests
import streamlit as st

# Absolute imports: `streamlit run` executes this file as __main__ rather than
# as a package module, so relative imports would fail at startup.
from mcfinex.config import Settings, database_ready as _database_ready
from mcfinex.db.store import Store
from mcfinex.enrich import enrich
from mcfinex.report import screen_all
from mcfinex.sources.screener import ScreenerError
from mcfinex.trends import TREND_LINES, analyse
from mcfinex.screening import Verdict

VERDICT_COLOUR = {
    Verdict.BUY.value: "background-color: #1b5e20; color: #ffffff",
    Verdict.SELL.value: "background-color: #7f1d1d; color: #ffffff",
    Verdict.HOLD.value: "background-color: #4a4a12; color: #ffffff",
    Verdict.UNKNOWN.value: "color: #888888",
}

FUNDAMENTAL_LABELS = [
    "Promoter holding", "Promoter pledge", "Reserves / capital", "Debt / equity",
    "Current ratio", "ROCE %", "Inventory turnover", "Free cash flow",
    "P/E vs sector", "Price / book", "Dividend yield %",
]
VALUATION_LABELS = [
    "EV/EBITDA upside %", "EV/EBITDA upside % (net debt)",
    "P/E re-rating % (yearly)", "P/E re-rating % (quarterly)",
]

# pandas Styler.format takes str.format specs, not printf ones. A printf spec
# such as "%.2f" is passed through verbatim, so every price rendered as the
# literal text "%.2f" rather than a number.
#: Always shown: what identifies a row and what every screen is judged on.
IDENTITY_COLUMNS = ["Ticker", "Company", "Sector", "Price",
                    "BUY signals", "SELL signals", "Scored"]
#: Shown when no particular signal is selected.
DEFAULT_NUMERIC_COLUMNS = ["EV/EBITDA target", "Upside %", "Entry 3/4", "Entry 2/3",
                           "PE yearly target", "PE quarterly target"]
#: Numeric columns each signal brings with it when selected on its own.
SIGNAL_COMPANIONS = {
    "EV/EBITDA upside %": ["EV/EBITDA target", "Upside %", "Entry 3/4", "Entry 2/3"],
    "EV/EBITDA upside % (net debt)": ["EV/EBITDA target", "Upside %"],
    "P/E re-rating % (yearly)": ["PE yearly target"],
    "P/E re-rating % (quarterly)": ["PE quarterly target"],
}

NUMERIC_FORMATS = {
    "Price": "{:,.2f}",
    "Upside %": "{:+.1f}",
    "EV/EBITDA target": "{:,.1f}",
    "Entry 3/4": "{:,.1f}",
    "Entry 2/3": "{:,.1f}",
    "PE yearly target": "{:,.1f}",
    "PE quarterly target": "{:,.1f}",
}


@st.cache_data(show_spinner="Screening companies...")
def load(db_path: str, revision: str) -> tuple[pd.DataFrame, dict]:
    """Screen everything once and cache it.

    Keyed on a revision token read from the database, so new data invalidates
    the cache on its own rather than waiting for someone to press refresh.
    """
    with Store(db_path) as store:
        rows = screen_all(store)
        detail = {r.screening.ticker: r for r in rows}
        frame = pd.DataFrame([r.as_record() for r in rows])
    return frame, detail


def main() -> None:
    st.title("Detailed screen")

    # Resolved per run rather than from the module-level singleton, which
    # freezes the environment at import time.
    settings = Settings.from_env()
    db_path = str(settings.db_path)
    if not _database_ready(settings):
        st.error(f"No database at {db_path}. Run `mcfinex init` then `mcfinex scrape`.")
        return

    with Store(db_path) as probe:
        revision = probe.revision()
    frame, detail = load(db_path, revision)
    if frame.empty:
        st.warning("Nothing scraped yet. Run `mcfinex scrape --from-template`.")
        return

    filtered, columns, measures = _sidebar(frame)
    _overview(frame, filtered)
    picked = _table(filtered, columns)
    _explain_row(picked, detail, measures)
    _drilldown(filtered, detail)


def _sidebar(frame: pd.DataFrame) -> pd.DataFrame:
    st.sidebar.header("Filters")
    if st.sidebar.button("Refresh data", width='stretch'):
        st.cache_data.clear()
        st.rerun()

    search = st.sidebar.text_input("Search ticker or company").strip().lower()
    sectors = sorted(s for s in frame["Sector"].dropna().unique())
    chosen = st.sidebar.multiselect("Sector", sectors)

    max_buys = int(frame["BUY signals"].max())
    min_buys = st.sidebar.slider("Minimum BUY signals", 0, max_buys, 0)
    max_sells = st.sidebar.slider("Maximum SELL signals", 0, int(frame["SELL signals"].max()),
                                  int(frame["SELL signals"].max()))
    only_upside = st.sidebar.checkbox("Only positive EV/EBITDA upside")

    signals, verdicts, match_all = _signal_filter(frame)

    out = frame[(frame["BUY signals"] >= min_buys) & (frame["SELL signals"] <= max_sells)]
    if chosen:
        out = out[out["Sector"].isin(chosen)]
    if search:
        mask = (out["Ticker"].str.lower().str.contains(search, na=False)
                | out["Company"].str.lower().str.contains(search, na=False))
        out = out[mask]
    if only_upside:
        out = out[out["Upside %"].fillna(-1) > 0]
    if signals and verdicts:
        out = out[_verdict_mask(out, signals, verdicts, match_all=match_all)]

    st.sidebar.caption(f"{len(out)} of {len(frame)} companies")
    return out, _visible_columns(frame, signals), signals


def _signal_filter(frame: pd.DataFrame):
    """Pick which measures to show, and which verdicts to keep.

    The two work together: choosing a measure narrows the columns, choosing a
    verdict narrows the rows, and choosing both asks "show me the companies this
    measure rates that way".
    """
    st.sidebar.divider()
    available = [c for c in FUNDAMENTAL_LABELS + VALUATION_LABELS if c in frame.columns]
    signals = st.sidebar.multiselect(
        "Measure", available,
        help="Which signals to show as columns. None selected shows them all.",
    )
    verdicts = st.sidebar.multiselect(
        "Verdict", [v.value for v in Verdict],
        help="Keep only companies whose selected measures read this way.",
    )
    match_all = False
    if len(signals) > 1 and verdicts:
        match_all = st.sidebar.radio(
            "Match", ["Any selected measure", "All selected measures"],
            help="Whether a company must satisfy one of the chosen measures or every one.",
        ) == "All selected measures"
    if verdicts and not signals:
        st.sidebar.caption("Pick a measure for the verdict filter to act on.")
    return signals, verdicts, match_all


def _verdict_mask(frame: pd.DataFrame, signals: list[str], verdicts: list[str],
                  *, match_all: bool) -> pd.Series:
    matches = [frame[column].isin(verdicts) for column in signals if column in frame.columns]
    if not matches:
        return pd.Series(True, index=frame.index)
    combined = matches[0]
    for other in matches[1:]:
        combined = (combined & other) if match_all else (combined | other)
    return combined


def _visible_columns(frame: pd.DataFrame, signals: list[str]) -> list[str]:
    """Identity columns, then whichever measures were asked for."""
    if not signals:
        wanted = DEFAULT_NUMERIC_COLUMNS + [
            c for c in FUNDAMENTAL_LABELS + VALUATION_LABELS if c in frame.columns
        ]
    else:
        companions: list[str] = []
        for signal in signals:
            for extra in SIGNAL_COMPANIONS.get(signal, []):
                if extra not in companions:
                    companions.append(extra)
        wanted = companions + signals
    ordered = IDENTITY_COLUMNS + [c for c in wanted if c not in IDENTITY_COLUMNS]
    return [c for c in ordered if c in frame.columns]


def _overview(frame: pd.DataFrame, filtered: pd.DataFrame) -> None:
    a, b, c, d = st.columns(4)
    a.metric("Companies", len(frame))
    b.metric("In view", len(filtered))
    strong = int((filtered["BUY signals"] >= 6).sum())
    c.metric("6+ BUY signals", strong)
    upside = filtered["Upside %"].dropna()
    d.metric("Median upside %", f"{upside.median():.1f}" if len(upside) else "-")


def _table(filtered: pd.DataFrame, columns: list[str]) -> str | None:
    st.subheader("Screen")
    st.caption("Select a row to see why its verdicts read the way they do.")
    shown = filtered[columns]
    verdict_cols = [c for c in shown.columns if c in FUNDAMENTAL_LABELS + VALUATION_LABELS]
    styled = (
        shown.sort_values(["BUY signals", "SELL signals"], ascending=[False, True])
        .style.map(lambda v: VERDICT_COLOUR.get(v, ""), subset=verdict_cols)
        .format({k: v for k, v in NUMERIC_FORMATS.items() if k in shown.columns}, na_rep="-")
    )
    ordered = shown.sort_values(["BUY signals", "SELL signals"], ascending=[False, True])
    event = st.dataframe(
        styled, width='stretch', height=520,
        key="screen-table", on_select="rerun", selection_mode="single-row",
    )
    st.download_button(
        "Download as CSV",
        shown.to_csv(index=False).encode(),
        file_name="mcfinex_screen.csv",
        mime="text/csv",
    )
    rows = event.selection["rows"] if event and event.selection else []
    return selected_ticker(ordered, rows)


def signals_to_explain(row, measures: list[str], ticker: str):
    """Which signals to open for a selected company, and the heading to use.

    With measures chosen in the sidebar, those are what the reader is asking
    about. Without any, the decisive signals -- the BUY and SELL ones -- are the
    ones that moved the score; HOLD and UNKNOWN did not.
    """
    signals = row.screening.signals
    if measures:
        return [s for s in signals if s.label in measures], f"{ticker}: {', '.join(measures)}"
    decisive = [s for s in signals if s.verdict in (Verdict.BUY, Verdict.SELL)]
    return decisive, f"{ticker}: signals that decided the score"


def selected_ticker(ordered: pd.DataFrame, rows: list[int]) -> str | None:
    """Map a dataframe selection back to a ticker.

    The index is into the sorted view the reader is looking at, not the frame's
    own order, so the frame must be sorted the same way before indexing.
    """
    if not rows:
        return None
    return ordered.iloc[rows[0]]["Ticker"]


def _explain_row(ticker: str | None, detail: dict, measures: list[str]) -> None:
    """Explain a company picked from the main screen.

    With measures chosen in the sidebar, those are what the reader is asking
    about, so only those are explained. Without any, the decisive signals -- the
    BUY and SELL ones -- are offered collapsed, since fourteen open panels would
    bury the answer they came for.
    """
    if ticker is None:
        return
    row = detail.get(ticker)
    if row is None:
        return

    wanted, heading = signals_to_explain(row, measures, ticker)
    st.markdown(f"#### {heading}")
    if not wanted:
        st.info("No decisive signals for this company.")
        return
    for signal in wanted:
        with st.expander(f"{signal.label} — {signal.verdict.value}",
                         expanded=len(wanted) == 1):
            _explain(signal)


def _drilldown(filtered: pd.DataFrame, detail: dict) -> None:
    if filtered.empty:
        return
    st.subheader("Company detail")
    ticker = st.selectbox(
        "Company",
        filtered["Ticker"].tolist(),
        format_func=lambda t: f"{t} — {detail[t].screening.name}" if t in detail else t,
    )
    row = detail.get(ticker)
    if row is None:
        return

    m, s = row.metrics, row.screening
    st.markdown(f"### {s.name}  \n{s.sector or '-'}")

    a, b, c, d = st.columns(4)
    a.metric("Price", f"{m.price:,.2f}" if m.price else "-")
    b.metric("EV/EBITDA target", f"{row.target_ev_ebitda:,.1f}" if row.target_ev_ebitda else "-")
    upside = m.ev_ebitda_upside
    b.caption(f"{upside:+.1f}% vs price" if upside is not None else "")
    c.metric("P/E", f"{m.stock_pe:,.1f}" if m.stock_pe else "-")
    c.caption(f"sector median {m.sector_pe:,.1f}" if m.sector_pe else "no sector median")
    d.metric("BUY signals", f"{s.buy_count} / {s.scored_count}")

    _enrich_control(ticker, row)

    st.caption("Select any signal below to see the numbers behind its verdict.")
    left, right = st.columns(2)
    with left:
        st.markdown("**Fundamentals**")
        chosen_fundamental = _selectable_signals(s.fundamentals, key=f"f-{ticker}")
    with right:
        st.markdown("**Valuation**")
        chosen_valuation = _selectable_signals(s.valuations, key=f"v-{ticker}")

    _explain(chosen_fundamental or chosen_valuation)

    with right:
        st.markdown("**Entry prices**")
        st.dataframe(
            pd.DataFrame([
                {"Basis": basis, "Price": _round(price)}
                for basis, price in (
                    ("Target", row.target_ev_ebitda),
                    ("Entry 3/4", row.entry_3by4),
                    ("Entry 2/3", row.entry_2by3),
                    ("P/E yearly target", row.target_pe_yearly),
                    ("P/E quarterly target", row.target_pe_quarterly),
                )
            ]),
            width='stretch', hide_index=True,
        )

    _quarterly_trends(ticker)


def _quarterly_trends(ticker: str) -> None:
    """Eight quarters of Sales, Operating Profit and EPS, with a projection.

    Year-over-year rather than quarter-on-quarter: quarterly results are
    seasonal, and comparing a December against a September measures the calendar
    rather than the business.
    """
    with Store(str(Settings.from_env().db_path)) as store:
        trends = [t for t in (_trend_for(store, ticker, label, aliases)
                              for label, aliases in TREND_LINES) if t]
    if not trends:
        return

    st.markdown("**Last eight quarters**")
    st.caption(
        "Growth is year on year, each quarter against the same quarter a year "
        "earlier. The projection assumes next quarter repeats last year's same "
        "quarter, grown at the recent rate — arithmetic, not a prediction."
    )
    for trend in trends:
        head, chart = st.columns([1, 2])
        with head:
            st.markdown(f"**{trend.label}**")
            if trend.ttm_growth_pct is not None:
                st.metric("TTM", f"{trend.ttm:,.0f}", f"{trend.ttm_growth_pct:+.1f}% YoY")
            if trend.forecast is not None:
                st.caption(
                    f"{trend.forecast_period}: **{trend.forecast:,.1f}** "
                    f"({trend.confidence.value.lower()} confidence)"
                )
            elif trend.note:
                st.caption(trend.note)
        with chart:
            st.altair_chart(_quarter_chart(trend), use_container_width=True)


def _quarter_chart(trend):
    """Eight discrete quarters, in order, as readable bars.

    Neither `st.bar_chart` default works here. A string index sorts
    alphabetically -- Dec 24, Dec 25, Jun 25, Jun 26, Mar 25 -- scrambling the
    trend; a datetime index is treated as continuous time, which draws hairline
    bars against a monthly axis. So the axis is ordinal with an explicit order.
    """
    import altair as alt

    labels = [f"{p:%b %y}" for p in trend.periods]
    frame = pd.DataFrame({"quarter": labels, "value": trend.values})
    return (
        alt.Chart(frame)
        .mark_bar(size=26, cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            x=alt.X("quarter:N", sort=labels, title=None,
                    axis=alt.Axis(labelAngle=0)),
            y=alt.Y("value:Q", title=None),
            tooltip=["quarter", "value"],
        )
        .properties(height=160)
    )


def _trend_for(store: Store, ticker: str, label: str, aliases: tuple[str, ...]):
    from datetime import date as _date

    for alias in aliases:
        rows = store.quarterly_history(ticker, alias)
        if rows:
            return analyse(label, [_date.fromisoformat(p) for p, _ in rows],
                           [v for _, v in rows])
    return None


def _round(value: float | None) -> float | None:
    """Prices to the paisa. Raw model output carries float noise (232.7939...)."""
    return None if value is None else round(value, 2)


def _enrich_control(ticker: str, row) -> None:
    """Fetch the collapsed balance-sheet detail for this one company.

    Three extra requests, so it is never part of a bulk run. Supplies cash,
    which changes enterprise value, and the current asset/liability split, which
    is the only way the current ratio can be computed -- so the company is
    re-valued and re-scored straight afterwards.
    """
    already = row.metrics.current_assets is not None
    label = "Re-fetch balance-sheet detail" if already else "Fetch balance-sheet detail"
    caption = (
        "Cash, current assets and liabilities are loaded; the valuation uses them."
        if already else
        "Adds cash, the current ratio and the long-term borrowings split, then re-values."
    )

    left, right = st.columns([1, 3])
    with left:
        clicked = st.button(label, key=f"enrich-{ticker}", width="stretch")
    with right:
        st.caption(caption)

    if not clicked:
        return
    with st.spinner(f"Fetching schedules for {ticker}..."):
        try:
            with Store(str(Settings.from_env().db_path)) as store:
                result = enrich(store, ticker)
        except (ScreenerError, requests.RequestException) as exc:
            st.error(f"Could not fetch detail for {ticker}: {exc}")
            return
    if not result.found_anything:
        st.warning(f"Screener has no balance-sheet schedules for {ticker}.")
        return
    st.success(
        f"{ticker}: cash {result.cash:,.0f}, current assets {result.current_assets:,.0f}, "
        f"current liabilities {result.current_liabilities:,.0f}"
        + (" — re-valued." if result.revalued else "")
    )
    st.cache_data.clear()
    st.rerun()


def _selectable_signals(signals, *, key: str):
    """Render a signal table whose rows can be clicked for the workings."""
    event = st.dataframe(
        _signal_frame(signals),
        width="stretch", hide_index=True, key=key,
        on_select="rerun", selection_mode="single-row",
    )
    rows = event.selection["rows"] if event and event.selection else []
    return signals[rows[0]] if rows else None


def _explain(signal) -> None:
    """Show why one signal reads the way it does."""
    if signal is None:
        return
    explanation = signal.explanation
    if explanation is None:
        st.info(f"{signal.label}: {signal.rule}. {signal.verdict.value}.")
        return

    with st.container(border=True):
        st.markdown(f"### {signal.label} — {signal.verdict.value}")
        st.write(explanation.reasoning)

        working, meaning = st.columns([1, 1])
        with working:
            st.markdown("**The numbers**")
            st.dataframe(
                pd.DataFrame(
                    [{"Input": name, "Value": _round(value)}
                     for name, value in explanation.inputs],
                ),
                width="stretch", hide_index=True,
            )
            st.caption(f"Formula: {explanation.formula}")
            arithmetic = explanation.arithmetic(signal.value)
            if arithmetic:
                st.code(arithmetic, language=None)
            st.caption(f"Threshold: {signal.rule}")
        with meaning:
            st.markdown("**What it means**")
            st.write(explanation.definition)
            st.markdown(f"[Read more on Investopedia]({explanation.url})")


def _signal_frame(signals) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Signal": s.label,
            "Verdict": s.verdict.value,
            "Value": None if s.value is None else round(s.value, 2),
            "Rule": s.rule,
        }
        for s in signals
    ])


if __name__ == "__main__":
    main()
