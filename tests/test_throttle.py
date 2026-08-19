"""Rate-limit handling.

A run at a fixed 0.7s delay was throttled from its 71st company and kept
hammering for 28 minutes, losing 716 of 2,258. These cover the behaviour that
prevents a repeat.
"""

from __future__ import annotations

import pytest
import requests

from mcfinex.sources.screener import RateLimited, ScreenerError, Throttle, fetch


class FakeResponse:
    def __init__(self, status: int, text: str = "", headers: dict | None = None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class FakeSession:
    """Returns the queued responses in order, recording every call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, url, **kw):
        self.calls += 1
        return self.responses.pop(0) if self.responses else FakeResponse(200, "<h1>ok</h1>")


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    monkeypatch.setattr("mcfinex.sources.screener.time.sleep", lambda _s: None)


class TestThrottle:
    def test_penalty_doubles_the_delay(self):
        t = Throttle(1.0)
        assert t.penalise() == 2.0
        assert t.penalise() == 4.0

    def test_penalty_lifts_a_tiny_delay_to_at_least_one_second(self):
        assert Throttle(0.1).penalise() == 1.0

    def test_penalty_is_capped(self):
        t = Throttle(1.0, maximum=8.0)
        for _ in range(10):
            t.penalise()
        assert t.delay == 8.0

    def test_reward_only_eases_off_after_a_streak(self):
        t = Throttle(1.0)
        t.penalise()
        for _ in range(19):
            t.reward()
        assert t.delay == 2.0        # not yet
        t.reward()
        assert t.delay < 2.0         # 20th success eases it

    def test_reward_never_goes_below_the_base_delay(self):
        t = Throttle(1.0)
        for _ in range(500):
            t.reward()
        assert t.delay == 1.0


class TestFetchBackoff:
    def test_retries_a_429_then_succeeds(self):
        session = FakeSession([FakeResponse(429), FakeResponse(200, "<h1>Acme</h1>")])
        html = fetch("ACME", session=session, throttle=Throttle(0.01))
        assert "Acme" in html
        assert session.calls == 2

    def test_gives_up_after_the_retry_budget(self):
        session = FakeSession([FakeResponse(429)] * 6)
        with pytest.raises(RateLimited, match="rate limited after 2 retries"):
            fetch("ACME", session=session, throttle=Throttle(0.01), retries=2)
        assert session.calls == 3  # the original plus two retries

    def test_rate_limiting_slows_the_shared_throttle(self):
        pace = Throttle(1.0)
        session = FakeSession([FakeResponse(429), FakeResponse(200, "<h1>ok</h1>")])
        fetch("ACME", session=session, throttle=pace)
        # The next company inherits the slower pace rather than rediscovering it.
        assert pace.delay == 2.0

    def test_retry_after_header_is_honoured(self, monkeypatch):
        slept = []
        monkeypatch.setattr("mcfinex.sources.screener.time.sleep", lambda s: slept.append(s))
        session = FakeSession([
            FakeResponse(429, headers={"Retry-After": "7"}),
            FakeResponse(200, "<h1>ok</h1>"),
        ])
        fetch("ACME", session=session, throttle=Throttle(0.5))
        assert 7 in slept

    def test_absurd_retry_after_is_clamped(self, monkeypatch):
        slept = []
        monkeypatch.setattr("mcfinex.sources.screener.time.sleep", lambda s: slept.append(s))
        session = FakeSession([
            FakeResponse(429, headers={"Retry-After": "99999"}),
            FakeResponse(200, "<h1>ok</h1>"),
        ])
        fetch("ACME", session=session, throttle=Throttle(0.5))
        assert max(slept) <= 300

    def test_unparseable_retry_after_falls_back(self, monkeypatch):
        monkeypatch.setattr("mcfinex.sources.screener.time.sleep", lambda _s: None)
        session = FakeSession([
            FakeResponse(429, headers={"Retry-After": "next tuesday"}),
            FakeResponse(200, "<h1>ok</h1>"),
        ])
        assert fetch("ACME", session=session, throttle=Throttle(0.01))

    def test_404_is_not_retried(self):
        # A missing company will still be missing; retrying just wastes requests.
        session = FakeSession([FakeResponse(404)])
        with pytest.raises(ScreenerError, match="no such company"):
            fetch("NOPE", session=session, throttle=Throttle(0.01))
        assert session.calls == 1

    def test_rate_limited_is_distinguishable_from_a_missing_company(self):
        assert issubclass(RateLimited, ScreenerError)
