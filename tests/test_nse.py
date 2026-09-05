import io
import zipfile

import pytest

from mcfinex.sources.nse import NseError, parse_bhavcopy

HEADER = "TradDt,Sgmt,ISIN,TckrSymb,SctySrs,FinInstrmNm,ClsPric"
ROWS = [
    "2026-08-14,CM,INE123A01011,ACME,EQ,Acme Ltd,101.50",
    "2026-08-14,CM,INE456B01022,BETA,BE,Beta Ltd,55.25",
    "2026-08-14,CM,IN0020200104,SGBJUN28,GB,Gold Bonds,14900.00",  # not equity
    "2026-08-14,CM,INE789C01033,GAMMA,EQ,Gamma Ltd,",              # no close price
]


def make_zip(lines):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("BhavCopy.csv", "\n".join(lines))
    return buffer.getvalue()


@pytest.fixture
def payload():
    return make_zip([HEADER, *ROWS])


class TestParseBhavcopy:
    def test_keeps_only_equity_series(self, payload):
        tickers = [l.ticker for l in parse_bhavcopy(payload)]
        assert tickers == ["ACME", "BETA", "GAMMA"]
        assert "SGBJUN28" not in tickers  # sovereign gold bonds have no company page

    def test_reads_isin_and_close(self, payload):
        acme = parse_bhavcopy(payload)[0]
        assert acme.isin == "INE123A01011"
        assert acme.close == 101.5
        assert acme.name == "Acme Ltd"

    def test_blank_close_becomes_none(self, payload):
        gamma = [l for l in parse_bhavcopy(payload) if l.ticker == "GAMMA"][0]
        assert gamma.close is None

    def test_fund_units_are_excluded(self):
        # ETFs trade in the EQ series but have no financials. Indian ISINs
        # distinguish them: INE is an equity share, INF a fund unit.
        payload = make_zip([HEADER, *ROWS,
                            "2026-08-14,CM,INF209KB1altered,ABSLNN50ET,EQ,Nifty ETF,78.32"])
        assert "ABSLNN50ET" not in [l.ticker for l in parse_bhavcopy(payload)]

    def test_equity_isins_are_kept(self):
        assert [l.ticker for l in parse_bhavcopy(make_zip([HEADER, ROWS[0]]))] == ["ACME"]

    def test_archive_without_csv_is_reported(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("readme.txt", "nothing")
        with pytest.raises(NseError, match="no CSV"):
            parse_bhavcopy(buffer.getvalue())

    def test_empty_listing_set_is_not_an_error(self):
        assert parse_bhavcopy(make_zip([HEADER])) == []


class TestUniverse:
    """A single bhavcopy lists only what traded, so sessions must be unioned."""

    def _patch(self, monkeypatch, by_day):
        from mcfinex.sources import nse as module

        def fake_fetch(day, **kw):
            return by_day.get(day)

        monkeypatch.setattr(module, "fetch_bhavcopy", fake_fetch)

    def test_unions_tickers_across_sessions(self, monkeypatch):
        from datetime import date

        from mcfinex.sources.nse import universe

        newer = make_zip([HEADER, "2026-08-18,CM,INE1,ACME,EQ,Acme,10.0"])
        older = make_zip([HEADER, "2026-08-17,CM,INE2,QUIET,EQ,Quiet Co,5.0"])
        self._patch(monkeypatch, {date(2026, 8, 18): newer, date(2026, 8, 17): older})
        listings, sessions = universe(days=2, on=date(2026, 8, 18))
        assert [l.ticker for l in listings] == ["ACME", "QUIET"]
        assert len(sessions) == 2

    def test_newest_session_wins_for_price(self, monkeypatch):
        from datetime import date

        from mcfinex.sources.nse import universe

        newer = make_zip([HEADER, "2026-08-18,CM,INE1,ACME,EQ,Acme,20.0"])
        older = make_zip([HEADER, "2026-08-17,CM,INE1,ACME,EQ,Acme,10.0"])
        self._patch(monkeypatch, {date(2026, 8, 18): newer, date(2026, 8, 17): older})
        listings, _ = universe(days=2, on=date(2026, 8, 18))
        assert listings[0].close == 20.0

    def test_skips_non_trading_days(self, monkeypatch):
        from datetime import date

        from mcfinex.sources.nse import universe

        payload = make_zip([HEADER, "2026-08-17,CM,INE1,ACME,EQ,Acme,10.0"])
        self._patch(monkeypatch, {date(2026, 8, 17): payload})  # 18th is a holiday
        listings, sessions = universe(days=1, on=date(2026, 8, 18))
        assert [l.ticker for l in listings] == ["ACME"]
        assert sessions == [date(2026, 8, 17)]

    def test_no_sessions_at_all_is_reported(self, monkeypatch):
        from datetime import date

        from mcfinex.sources.nse import NseError, universe

        self._patch(monkeypatch, {})
        with pytest.raises(NseError, match="no bhavcopy"):
            universe(days=2, on=date(2026, 8, 18))


class TestTransientFailures:
    """nsearchives is intermittently slow, and a slow response is not an answer.

    The walk-back was built so one unusable day could not sink the run, but it
    only ever handled 404s: a timeout raised straight out of the loop. On a
    Saturday that killed a job while fetching a file it did not need, before it
    reached the Friday one it did.
    """

    def responses(self, monkeypatch, *outcomes):
        """Drive fetch_bhavcopy with a scripted sequence of HTTP outcomes."""
        from mcfinex.sources import nse as module

        monkeypatch.setattr(module.time, "sleep", lambda _s: None)
        calls = []

        class Resp:
            def __init__(self, status, body=b""):
                self.status_code, self.content = status, body

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise module.requests.HTTPError(str(self.status_code), response=self)

        class Session:
            def get(self, url, **kw):
                calls.append(url)
                outcome = outcomes[min(len(calls), len(outcomes)) - 1]
                if isinstance(outcome, Exception):
                    raise outcome
                return Resp(*outcome) if isinstance(outcome, tuple) else Resp(outcome)

        return Session(), calls

    def test_a_timeout_is_retried_and_can_succeed(self, monkeypatch):
        from datetime import date

        from mcfinex.sources.nse import fetch_bhavcopy
        import requests

        sess, calls = self.responses(monkeypatch, requests.Timeout(), (200, b"zip"))
        assert fetch_bhavcopy(date(2026, 9, 4), session=sess) == b"zip"
        assert len(calls) == 2

    def test_it_gives_up_after_the_last_attempt(self, monkeypatch):
        from datetime import date

        from mcfinex.sources.nse import fetch_bhavcopy
        import requests

        sess, calls = self.responses(monkeypatch, requests.Timeout())
        with pytest.raises(requests.Timeout):
            fetch_bhavcopy(date(2026, 9, 4), session=sess)
        assert len(calls) == 3

    def test_a_404_is_an_answer_and_is_not_retried(self, monkeypatch):
        from datetime import date

        from mcfinex.sources.nse import fetch_bhavcopy

        sess, calls = self.responses(monkeypatch, 404)
        assert fetch_bhavcopy(date(2026, 9, 5), session=sess) is None
        assert len(calls) == 1

    def test_a_404_is_not_retried_even_though_5xx_is(self, monkeypatch):
        from datetime import date

        from mcfinex.sources.nse import fetch_bhavcopy
        import requests

        sess, calls = self.responses(monkeypatch, 503, (200, b"zip"))
        assert fetch_bhavcopy(date(2026, 9, 4), session=sess) == b"zip"
        assert len(calls) == 2

    def test_a_renamed_url_fails_fast(self, monkeypatch):
        # A 403/410 is a real answer about the URL. Retrying only wastes the run.
        from datetime import date

        from mcfinex.sources.nse import fetch_bhavcopy
        import requests

        sess, calls = self.responses(monkeypatch, 403)
        with pytest.raises(requests.HTTPError):
            fetch_bhavcopy(date(2026, 9, 4), session=sess)
        assert len(calls) == 1

    def test_one_dead_day_no_longer_sinks_the_walk_back(self, monkeypatch):
        # Saturday times out; Friday's data must still be found.
        from datetime import date

        from mcfinex.sources import nse as module
        from mcfinex.sources.nse import universe
        import requests

        friday = make_zip([HEADER, "2026-09-04,CM,INE1,ACME,EQ,Acme,10.0"])

        def fetch(day, **kw):
            if day == date(2026, 9, 5):
                raise requests.Timeout("nsearchives is slow")
            return friday if day == date(2026, 9, 4) else None

        monkeypatch.setattr(module, "fetch_bhavcopy", fetch)
        listings, sessions = universe(days=1, on=date(2026, 9, 5))
        assert [l.ticker for l in listings] == ["ACME"]
        assert sessions == [date(2026, 9, 4)]

    def test_a_total_outage_still_raises(self, monkeypatch):
        # Skipping must not turn "NSE is unreachable" into an empty success.
        from datetime import date

        from mcfinex.sources import nse as module
        from mcfinex.sources.nse import NseError, universe
        import requests

        def fetch(day, **kw):
            raise requests.ConnectionError("down")

        monkeypatch.setattr(module, "fetch_bhavcopy", fetch)
        with pytest.raises(NseError, match="no bhavcopy"):
            universe(days=2, on=date(2026, 9, 5))

    def test_latest_bhavcopy_also_survives_a_dead_day(self, monkeypatch):
        from datetime import date

        from mcfinex.sources import nse as module
        from mcfinex.sources.nse import latest_bhavcopy
        import requests

        def fetch(day, **kw):
            if day == date(2026, 9, 5):
                raise requests.Timeout("slow")
            return b"payload" if day == date(2026, 9, 4) else None

        monkeypatch.setattr(module, "fetch_bhavcopy", fetch)
        day, payload = latest_bhavcopy(on=date(2026, 9, 5))
        assert (day, payload) == (date(2026, 9, 4), b"payload")
