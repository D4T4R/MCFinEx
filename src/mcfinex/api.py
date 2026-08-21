"""Read-only HTTP API over the screened database.

Serves what has already been scraped. Nothing here touches screener or NSE --
refreshing data is what `scrape`, `prices` and `enrich` are for, and a page load
must never trigger a network fetch.

Exists so the landing page is not tied to Streamlit. The screening logic sits in
:mod:`mcfinex.screening` and :mod:`mcfinex.picks`, which import no framework, so
a React front end can consume these endpoints without any of it moving.

Run with ``mcfinex-api`` or ``uvicorn mcfinex.api:app``.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import picks as picks_module
from .config import Settings
from .db.store import Store
from .disclaimer import FULL as FULL_DISCLAIMER, SHORT as SHORT_DISCLAIMER
from .report import screen_all
from .trends import TREND_LINES, analyse

app = FastAPI(
    title="MCFinEx",
    version="1.0.0",
    summary="Screened Indian equities, served from the local database.",
    description=FULL_DISCLAIMER,
)

# The landing page may be served from a different origin (a React dev server on
# :5173, Streamlit on :8501). Local-only tool, so this stays permissive.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

def _store() -> Store:
    return Store(Settings.from_env().db_path)


@lru_cache(maxsize=1)
def _screened(stamp: str) -> list:
    """Screen the whole universe once per underlying database change.

    Keyed on the database's modification time so an ordinary request never pays
    for a re-screen, but a scrape or price refresh invalidates it without a
    restart.
    """
    with _store() as store:
        return screen_all(store)


def _rows() -> list:
    path = Settings.from_env().db_path
    stamp = str(path.stat().st_mtime_ns) if path.exists() else "missing"
    return _screened(stamp)


def _as_dict(pick: picks_module.Pick) -> dict[str, Any]:
    return {
        "ticker": pick.ticker,
        "name": pick.name,
        "sector": pick.sector,
        "price": pick.price,
        "target": pick.target,
        "entry_3by4": pick.entry_3by4,
        "entry_2by3": pick.entry_2by3,
        "upside_pct": pick.upside_pct,
        "discount_to_entry_pct": pick.discount_to_entry_pct,
        "actionable": pick.is_actionable,
        "buy_signals": pick.buy_signals,
        "sell_signals": pick.sell_signals,
        "scored": pick.scored,
        "models_agreeing": pick.models_agreeing,
        "tier": pick.tier.value,
        "flags": pick.flags,
    }


@app.get("/health")
def health() -> dict[str, Any]:
    path = Settings.from_env().db_path
    if not path.exists():
        raise HTTPException(503, f"no database at {path}")
    return {"status": "ok", "database": str(path), "companies": len(_rows())}


@app.get("/picks")
def list_picks(
    tier: str | None = Query(None, description="High conviction | Below entry price | Watch"),
    sector: str | None = None,
    limit: int = Query(60, ge=1, le=500),
) -> dict[str, Any]:
    """Ranked candidates, best corroborated first."""
    chosen = None
    if tier:
        try:
            chosen = picks_module.Tier(tier)
        except ValueError:
            raise HTTPException(422, f"unknown tier {tier!r}")

    ranked = picks_module.rank(_rows(), tier=chosen)
    if sector:
        wanted = sector.casefold()
        ranked = [p for p in ranked if (p.sector or "").casefold() == wanted]
    return {
        "count": len(ranked),
        "picks": [_as_dict(p) for p in ranked[:limit]],
        "disclaimer": SHORT_DISCLAIMER,
    }


@app.get("/summary")
def summary() -> dict[str, Any]:
    """Headline counts for the landing page."""
    rows = _rows()
    tiers: dict[str, int] = {}
    for row in rows:
        tiers[picks_module.classify(row).value] = tiers.get(picks_module.classify(row).value, 0) + 1
    with _store() as store:
        priced = store.data_freshness()
    return {
        "companies": len(rows),
        "tiers": tiers,
        "price_date": priced[0],
        "last_scraped": priced[1],
        "generated": date.today().isoformat(),
    }


@app.get("/sectors")
def sectors(limit: int = Query(15, ge=1, le=200)) -> dict[str, Any]:
    """Where high-conviction names are clustering."""
    heat = picks_module.sector_heat(_rows(), limit=limit)
    return {
        "sectors": [
            {
                "sector": h.sector, "picks": h.picks, "total": h.total,
                "share_pct": h.share_pct, "median_upside_pct": h.median_upside_pct,
            }
            for h in heat
        ]
    }


@app.get("/company/{ticker}")
def company(ticker: str) -> dict[str, Any]:
    """Everything the detail view shows for one company, trends included."""
    ticker = ticker.upper()
    row = next((r for r in _rows() if r.screening.ticker == ticker), None)
    if row is None:
        raise HTTPException(404, f"{ticker} not in the database")

    with _store() as store:
        trends = [_trend(store, ticker, label, aliases) for label, aliases in TREND_LINES]

    return {
        **_as_dict(picks_module.to_pick(row)),
        "targets": {
            "ev_ebitda": row.target_ev_ebitda,
            "pe_yearly": row.target_pe_yearly,
            "pe_quarterly": row.target_pe_quarterly,
        },
        "signals": [
            {
                "key": s.key, "label": s.label, "verdict": s.verdict.value,
                "value": s.value, "rule": s.rule, "available": s.available,
            }
            for s in row.screening.signals
        ],
        "trends": [t for t in trends if t],
        "disclaimer": SHORT_DISCLAIMER,
    }


def _trend(store: Store, ticker: str, label: str, aliases: tuple[str, ...]) -> dict | None:
    """Eight-quarter trend for one line item."""
    for alias in aliases:
        rows = store.quarterly_history(ticker, alias)
        if not rows:
            continue
        periods = [date.fromisoformat(p) for p, _ in rows]
        trend = analyse(label, periods, [v for _, v in rows])
        return {
            "label": trend.label,
            "periods": [f"{p:%b %Y}" for p in trend.periods],
            "values": trend.values,
            "yoy_growth_pct": trend.yoy_growth,
            "ttm": trend.ttm,
            "ttm_prior": trend.ttm_prior,
            "ttm_growth_pct": trend.ttm_growth_pct,
            "forecast": trend.forecast,
            "forecast_period": trend.forecast_period,
            "confidence": trend.confidence.value,
            "note": trend.note,
        }
    return None
