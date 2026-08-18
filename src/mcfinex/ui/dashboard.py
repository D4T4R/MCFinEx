"""Streamlit screener.

Replaces the workbook's Results sheet. Presentation only -- every threshold and
verdict comes from :mod:`mcfinex.screening`, which imports nothing from here, so
this can be swapped for a different front end without touching the logic.

Run with ``mcfinex-dashboard`` or ``streamlit run src/mcfinex/ui/dashboard.py``.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

# Absolute imports: `streamlit run` executes this file as __main__ rather than
# as a package module, so relative imports would fail at startup.
from mcfinex.config import Settings
from mcfinex.db.store import Store
from mcfinex.report import screen_all
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


@st.cache_data(show_spinner="Screening companies...")
def load(db_path: str) -> tuple[pd.DataFrame, dict]:
    """Screen everything once and cache it.

    Keyed on the database path; use the sidebar refresh after a scrape.
    """
    with Store(db_path) as store:
        rows = screen_all(store)
        detail = {r.screening.ticker: r for r in rows}
        frame = pd.DataFrame([r.as_record() for r in rows])
    return frame, detail


def main() -> None:
    st.set_page_config(page_title="MCFinEx Screener", page_icon="📈", layout="wide")
    st.title("MCFinEx Screener")

    # Resolved per run rather than from the module-level singleton, which
    # freezes the environment at import time.
    settings = Settings.from_env()
    db_path = str(settings.db_path)
    if not settings.db_path.exists():
        st.error(f"No database at {db_path}. Run `mcfinex init` then `mcfinex scrape`.")
        return

    frame, detail = load(db_path)
    if frame.empty:
        st.warning("Nothing scraped yet. Run `mcfinex scrape --from-template`.")
        return

    filtered = _sidebar(frame)
    _overview(frame, filtered)
    _table(filtered)
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

    out = frame[(frame["BUY signals"] >= min_buys) & (frame["SELL signals"] <= max_sells)]
    if chosen:
        out = out[out["Sector"].isin(chosen)]
    if search:
        mask = (out["Ticker"].str.lower().str.contains(search, na=False)
                | out["Company"].str.lower().str.contains(search, na=False))
        out = out[mask]
    if only_upside:
        out = out[out["Upside %"].fillna(-1) > 0]

    st.sidebar.caption(f"{len(out)} of {len(frame)} companies")
    return out


def _overview(frame: pd.DataFrame, filtered: pd.DataFrame) -> None:
    a, b, c, d = st.columns(4)
    a.metric("Companies", len(frame))
    b.metric("In view", len(filtered))
    strong = int((filtered["BUY signals"] >= 6).sum())
    c.metric("6+ BUY signals", strong)
    upside = filtered["Upside %"].dropna()
    d.metric("Median upside %", f"{upside.median():.1f}" if len(upside) else "-")


def _table(filtered: pd.DataFrame) -> None:
    st.subheader("Screen")
    verdict_cols = [c for c in filtered.columns if c in FUNDAMENTAL_LABELS + VALUATION_LABELS]
    numeric = {
        "Price": "%.2f", "Upside %": "%.1f", "EV/EBITDA target": "%.1f",
        "Entry 3/4": "%.1f", "Entry 2/3": "%.1f",
        "PE yearly target": "%.1f", "PE quarterly target": "%.1f",
    }
    styled = (
        filtered.sort_values(["BUY signals", "SELL signals"], ascending=[False, True])
        .style.map(lambda v: VERDICT_COLOUR.get(v, ""), subset=verdict_cols)
        .format(numeric, na_rep="-")
    )
    st.dataframe(styled, width='stretch', height=520)
    st.download_button(
        "Download as CSV",
        filtered.to_csv(index=False).encode(),
        file_name="mcfinex_screen.csv",
        mime="text/csv",
    )


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

    left, right = st.columns(2)
    with left:
        st.markdown("**Fundamentals**")
        st.dataframe(_signal_frame(s.fundamentals), width='stretch', hide_index=True)
    with right:
        st.markdown("**Valuation**")
        st.dataframe(_signal_frame(s.valuations), width='stretch', hide_index=True)
        st.markdown("**Entry prices**")
        st.dataframe(
            pd.DataFrame([
                {"Basis": "Target", "Price": row.target_ev_ebitda},
                {"Basis": "Entry 3/4", "Price": row.entry_3by4},
                {"Basis": "Entry 2/3", "Price": row.entry_2by3},
                {"Basis": "P/E yearly target", "Price": row.target_pe_yearly},
                {"Basis": "P/E quarterly target", "Price": row.target_pe_quarterly},
            ]),
            width='stretch', hide_index=True,
        )


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
