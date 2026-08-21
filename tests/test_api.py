"""The read-only HTTP layer.

The API must never reach the network: a page load serves what is already in the
database, and refreshing is what `scrape`, `prices` and `enrich` are for.
"""

from __future__ import annotations

import pytest

from mcfinex.db.store import Store

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "api.db"
    with Store(db) as store:
        store.create_schema()
        for ticker, name, price, target in (
            ("CHEAP", "Cheap Ltd", 50.0, 200.0),
            ("FAIR", "Fair Ltd", 190.0, 200.0),
        ):
            store.upsert_company(ticker, {
                "name": name, "current_price": price, "market_cap": 1000.0,
                "industry": "Widgets", "roce": 25.0, "dividend_yield": 3.0,
                "book_value": price * 2, "stock_pe": 10.0,
                "outstanding_shares": 10.0, "last_updated": "2026-01-01",
                "price_date": "2026-08-18",
            })
            store.replace_financials(ticker, [
                ("2025-03-31", "balance-sheet", "Reserves", 900.0),
                ("2025-03-31", "balance-sheet", "Equity Capital", 100.0),
                ("2025-03-31", "shareholding", "Promoters", 70.0),
                *[(f"202{4 + i // 4}-{(i % 4) * 3 + 3:02d}-28", "quarters", "EPS in Rs", 2.0 + i)
                  for i in range(8)],
                *[(f"202{4 + i // 4}-{(i % 4) * 3 + 3:02d}-28", "quarters", "Sales", 100.0 + i)
                  for i in range(8)],
            ])
            store.replace_valuations(ticker, "ev_ebitda", {
                "target_price": target, "difference_pct": (target - price) / price * 100,
                "entry_price_1by4": target * 0.75, "entry_price_1by3": target * 0.66,
            })
            store.replace_valuations(ticker, "eps_yearly", {"target_price": target * 0.9})
            store.replace_valuations(ticker, "eps_quarterly", {"target_price": target * 0.85})
            store.replace_valuations(ticker, "derived", {"free_cash_flow": 10.0})

    monkeypatch.setenv("MCFINEX_DB", str(db))
    monkeypatch.setenv("MCFINEX_DATA_DIR", str(tmp_path))
    import mcfinex.api as api

    api._screened.cache_clear()
    return TestClient(api.app)


class TestHealth:
    def test_reports_the_database(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["companies"] == 2


class TestPicks:
    def test_returns_ranked_picks(self, client):
        body = client.get("/picks").json()
        assert body["count"] >= 1
        assert body["picks"][0]["ticker"]

    def test_cheap_company_is_actionable(self, client):
        picks = {p["ticker"]: p for p in client.get("/picks").json()["picks"]}
        assert picks["CHEAP"]["actionable"] is True

    def test_tier_filter(self, client):
        body = client.get("/picks", params={"tier": "High conviction"}).json()
        assert all(p["tier"] == "High conviction" for p in body["picks"])

    def test_unknown_tier_is_rejected(self, client):
        assert client.get("/picks", params={"tier": "Nonsense"}).status_code == 422

    def test_sector_filter(self, client):
        assert client.get("/picks", params={"sector": "Widgets"}).json()["count"] >= 1
        assert client.get("/picks", params={"sector": "Nothing"}).json()["count"] == 0

    def test_limit_is_bounded(self, client):
        assert client.get("/picks", params={"limit": 0}).status_code == 422
        assert client.get("/picks", params={"limit": 9999}).status_code == 422


class TestSummary:
    def test_reports_tier_counts_and_dates(self, client):
        body = client.get("/summary").json()
        assert body["companies"] == 2
        assert body["price_date"] == "2026-08-18"
        assert sum(body["tiers"].values()) == 2


class TestCompany:
    def test_returns_signals_and_trends(self, client):
        body = client.get("/company/CHEAP").json()
        assert body["ticker"] == "CHEAP"
        assert len(body["signals"]) == 14
        labels = {t["label"] for t in body["trends"]}
        assert {"Sales", "EPS in Rs"} <= labels

    def test_trend_carries_a_forecast(self, client):
        trends = {t["label"]: t for t in client.get("/company/CHEAP").json()["trends"]}
        assert trends["Sales"]["forecast"] is not None
        assert trends["Sales"]["confidence"] in {"HIGH", "MEDIUM", "LOW", "NONE"}

    def test_ticker_is_case_insensitive(self, client):
        assert client.get("/company/cheap").status_code == 200

    def test_unknown_ticker_is_404(self, client):
        assert client.get("/company/NOPE").status_code == 404


class TestNoNetwork:
    def test_endpoints_never_fetch(self, client, monkeypatch):
        # A page load must serve stored data; refreshing is the CLI's job.
        def explode(*a, **k):
            raise AssertionError("the API made a network request")

        monkeypatch.setattr("requests.Session.get", explode)
        monkeypatch.setattr("requests.get", explode)
        for path in ("/health", "/summary", "/picks", "/sectors", "/company/CHEAP"):
            assert client.get(path).status_code == 200


class TestDisclaimer:
    """Consumers of the API get the caveat too, not just visitors to the page."""

    def test_picks_carry_it(self, client):
        assert "disclaimer" in client.get("/picks").json()

    def test_company_detail_carries_it(self, client):
        assert "disclaimer" in client.get("/company/CHEAP").json()

    def test_it_is_in_the_openapi_description(self, client):
        spec = client.get("/openapi.json").json()
        assert "not investment advice" in spec["info"]["description"].lower()
