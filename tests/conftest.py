"""Test-wide isolation.

MCFINEX_PG points at the deployed database and takes precedence over
MCFINEX_DB, so a developer with it exported would have every test that sets up
a temporary SQLite file silently run against production instead. Cleared for
the whole session; a test that wants it sets it explicitly.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_production_database(monkeypatch):
    monkeypatch.delenv("MCFINEX_PG", raising=False)
