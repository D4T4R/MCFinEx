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
