"""The BSE bhavcopy.

Every test here exists because BSE differs from NSE in a way that fails quietly.

It answers a request for a non-trading day with HTTP 200 and an HTML page. Parsed
as CSV that yields no rows, and a price update only touches companies it already
knows -- so no rows writes nothing, raises nothing, and reports success. That is
a silent no-op every weekend, and it is what most of this file guards.

Its ``SctySrs`` vocabulary is entirely different: EQ and BE appear nowhere, so the
NSE series filter keeps zero BSE rows.

And series F carries bonds with INE ISINs, so the ISIN prefix that separates
equity from fund units on NSE does not separate equity from debt here.
"""

from __future__ import annotations

import pytest
import requests

from mcfinex.sources import bse
from mcfinex.sources.bhavcopy import looks_like_udiff

HEADER = "TradDt,Sgmt,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,FinInstrmNm,ClsPric"
ROWS = [
    "2026-09-07,CM,STK,500002,INE117A01022,ABB,A,ABB INDIA LIMITED,7397.10",
    "2026-09-07,CM,STK,500003,INE208C01025,AEGISLOG,B,AEGIS LOGISTICS LTD.,1253.50",
    "2026-09-07,CM,STK,500123,INE111A01025,SMALLCO,X,Small Co Ltd,12.40",
    "2026-09-07,CM,STK,543210,INE08RT01016,VALENCIA,M,Valencia Nutrition Limited,88.00",
    # Series F with an INE ISIN: a bond, not a company.
    "2026-09-07,CM,STK,975001,INE134E07588,760PFCL35,F,PFC-7.60%-3B-17-10-35-BOND,102.50",
    # Government security.
    "2026-09-07,CM,STK,800001,IN0020200104,GS2030,G,GOI Loan 2030,99.10",
    # An ETF: fund ISIN.
    "2026-09-07,CM,STK,590100,INF205KA1BP1,IVZINGOLD,E,INVESCO India Gold ETF,68.00",
    # A rights entitlement -- a temporary instrument, not a company.
    "2026-09-07,CM,STK,890123,INE0M8920010,CONTAIN-RE,R,CONTAINE TECHNOLOGIES LIMITED,3.10",
]

HTML_ERROR = (
    b'<!DOCTYPE html><html lang="en" data-critters-container>'
    b"<head><title>BSE Ltd</title></head><body>page unavailable</body></html>"
)


def csv_bytes(lines) -> bytes:
    return "\n".join(lines).encode("utf-8")


@pytest.fixture
def payload():
    return csv_bytes([HEADER, *ROWS])


class TestLooksLikeUdiff:
    def test_a_real_bhavcopy_passes(self, payload):
        assert looks_like_udiff(payload)

    def test_the_html_error_page_fails(self):
        # The whole point: this arrives with HTTP 200.
        assert not looks_like_udiff(HTML_ERROR)

    def test_a_utf8_bom_does_not_defeat_it(self):
        assert looks_like_udiff(b"\xef\xbb\xbf" + csv_bytes([HEADER]))

    def test_an_empty_body_fails(self):
        assert not looks_like_udiff(b"")

    def test_it_checks_for_the_header_not_against_html(self):
        # Anything unexpected should fail, not only the one error page BSE
        # happens to serve today.
        assert not looks_like_udiff(b'{"error": "nope"}')
        assert not looks_like_udiff(b"Gateway Timeout")


class TestParse:
    def test_main_board_and_sme_equity_are_kept(self, payload):
        tickers = [l.ticker for l in bse.parse_bhavcopy(payload)]
        assert tickers == ["ABB", "AEGISLOG", "SMALLCO", "VALENCIA"]

    def test_bonds_are_dropped_despite_an_equity_isin(self, payload):
        # INE134E07588 starts with INE, so the fund-unit rule that works on NSE
        # would have let this through. Only the group separates it.
        assert "760PFCL35" not in [l.ticker for l in bse.parse_bhavcopy(payload)]

    def test_government_securities_are_dropped(self, payload):
        assert "GS2030" not in [l.ticker for l in bse.parse_bhavcopy(payload)]

    def test_etfs_are_dropped(self, payload):
        assert "IVZINGOLD" not in [l.ticker for l in bse.parse_bhavcopy(payload)]

    def test_rights_entitlements_are_dropped(self, payload):
        assert "CONTAIN-RE" not in [l.ticker for l in bse.parse_bhavcopy(payload)]

    def test_it_reads_isin_name_and_close(self, payload):
        abb = bse.parse_bhavcopy(payload)[0]
        assert abb.isin == "INE117A01022"
        assert abb.close == 7397.10
        assert abb.name == "ABB INDIA LIMITED"

    def test_listings_are_stamped_with_the_exchange(self, payload):
        # The merge reports which exchange priced each company.
        assert {l.exchange for l in bse.parse_bhavcopy(payload)} == {"BSE"}

    def test_the_nse_series_filter_would_keep_nothing(self, payload):
        from mcfinex.sources.nse import EQUITY_SERIES

        assert bse.parse_bhavcopy(payload, groups=EQUITY_SERIES) == []

    def test_main_board_excludes_sme_and_illiquid(self, payload):
        assert [l.ticker for l in bse.main_board(payload)] == ["ABB", "AEGISLOG"]

    def test_html_handed_to_the_parser_is_an_error_not_an_empty_list(self):
        # Returning [] here is the silent failure this whole module is about.
        with pytest.raises(bse.BseError, match="not a BSE bhavcopy"):
            bse.parse_bhavcopy(HTML_ERROR)

    def test_a_header_with_no_rows_is_fine(self):
        assert bse.parse_bhavcopy(csv_bytes([HEADER])) == []


class TestFetch:
    def _respond(self, monkeypatch, status, body):
        class Response:
            status_code = status
            content = body
            ok = 200 <= status < 300

            def raise_for_status(self):
                if not self.ok:
                    raise requests.HTTPError(f"{status}", response=self)

        class Session:
            def get(self, url, **kwargs):
                return Response()

        monkeypatch.setattr(bse.time, "sleep", lambda _s: None)
        return Session()

    def test_a_real_body_is_returned(self, monkeypatch, payload):
        sess = self._respond(monkeypatch, 200, payload)
        assert bse.fetch_bhavcopy(bse.date(2026, 9, 7), session=sess) == payload

    def test_a_200_with_html_is_treated_as_absent(self, monkeypatch):
        # The trap. Without this the walk-back stops on the first weekend it
        # touches and hands HTML onwards.
        sess = self._respond(monkeypatch, 200, HTML_ERROR)
        assert bse.fetch_bhavcopy(bse.date(2026, 9, 6), session=sess) is None

    def test_a_404_is_treated_as_absent(self, monkeypatch):
        sess = self._respond(monkeypatch, 404, b"")
        assert bse.fetch_bhavcopy(bse.date(2026, 9, 6), session=sess) is None

    def test_a_500_is_retried_then_raises(self, monkeypatch):
        calls = []

        class Response:
            status_code = 503
            content = b""
            ok = False

            def raise_for_status(self):
                raise requests.HTTPError("503", response=self)

        class Session:
            def get(self, url, **kwargs):
                calls.append(url)
                return Response()

        monkeypatch.setattr(bse.time, "sleep", lambda _s: None)
        with pytest.raises(requests.HTTPError):
            bse.fetch_bhavcopy(bse.date(2026, 9, 7), session=Session())
        assert len(calls) == bse.RETRY_ATTEMPTS

    def test_the_url_is_the_bare_csv(self):
        # The .csv.zip sibling answers 200 with the same HTML page, on trading
        # days too, so it must not be reached for.
        url = bse.BHAVCOPY_URL.format(yyyymmdd="20260907")
        assert url.endswith("BhavCopy_BSE_CM_0_0_0_20260907_F_0000.CSV")
        assert ".zip" not in url


class TestWalkBack:
    def _patch(self, monkeypatch, by_day):
        def fake(day, *, session=None, **kwargs):
            return by_day.get(day)

        monkeypatch.setattr(bse, "fetch_bhavcopy", fake)

    def test_it_finds_the_most_recent_session(self, monkeypatch, payload):
        friday = bse.date(2026, 9, 4)
        self._patch(monkeypatch, {friday: payload})
        day, got = bse.latest_bhavcopy(on=bse.date(2026, 9, 6))
        assert day == friday
        assert got == payload

    def test_it_gives_up_rather_than_looping(self, monkeypatch):
        self._patch(monkeypatch, {})
        with pytest.raises(bse.BseError, match="no bse bhavcopy"):
            bse.latest_bhavcopy(on=bse.date(2026, 9, 6), max_lookback=3)

    def test_universe_unions_sessions_and_keeps_the_newest_price(self, monkeypatch):
        newer = csv_bytes([HEADER,
                           "2026-09-07,CM,STK,1,INE117A01022,ABB,A,ABB,7397.10"])
        older = csv_bytes([HEADER,
                           "2026-09-04,CM,STK,1,INE117A01022,ABB,A,ABB,7000.00",
                           "2026-09-04,CM,STK,2,INE208C01025,AEGISLOG,B,Aegis,1200.00"])
        self._patch(monkeypatch, {bse.date(2026, 9, 7): newer,
                                 bse.date(2026, 9, 4): older})
        listings, sessions = bse.universe(days=2, on=bse.date(2026, 9, 7))
        assert len(sessions) == 2
        by_ticker = {l.ticker: l for l in listings}
        # Both seen, and the newer close wins for the one in both files.
        assert set(by_ticker) == {"ABB", "AEGISLOG"}
        assert by_ticker["ABB"].close == 7397.10

    def test_universe_dedupes_on_isin_not_ticker(self, monkeypatch):
        # BSE reuses symbols across instrument types; the ISIN is the identity.
        rows = csv_bytes([HEADER,
                          "2026-09-07,CM,STK,1,INE117A01022,ABB,A,ABB India,7397.10",
                          "2026-09-07,CM,STK,2,INE999Z01011,ABB,B,Other ABB,10.00"])
        self._patch(monkeypatch, {bse.date(2026, 9, 7): rows})
        listings, _ = bse.universe(days=1, on=bse.date(2026, 9, 7))
        assert len({l.isin for l in listings}) == 2


class TestGroups:
    def test_main_board_is_a_subset_of_equity(self):
        assert bse.MAIN_BOARD_GROUPS <= bse.EQUITY_GROUPS

    def test_the_non_company_groups_are_excluded_from_both(self):
        # F is debt, G government securities, E ETFs, IF InvITs and REITs,
        # R rights entitlements.
        for group in ("F", "G", "E", "IF", "R"):
            assert group not in bse.EQUITY_GROUPS
            assert group not in bse.MAIN_BOARD_GROUPS
