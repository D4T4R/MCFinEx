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
