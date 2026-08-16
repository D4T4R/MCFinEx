import pytest
from openpyxl import Workbook, load_workbook

from mcfinex.db.store import Store
from mcfinex.export import workbook as wb
from mcfinex.export.workbook import WorkbookError, populate


@pytest.fixture
def template(tmp_path):
    """A stand-in for SSP_Working_merged.xlsx: 3 header rows, formulas intact."""
    book = Workbook()
    sheet = book.active
    sheet.title = wb.DATA_SHEET
    sheet.cell(3, wb.TICKER_COLUMN).value = "TICKER"
    # One existing company, plus a formula that must survive the round trip.
    sheet.cell(4, wb.TICKER_COLUMN).value = "ACME"
    sheet.cell(4, 10).value = "=H4/I4"
    path = tmp_path / "template.xlsx"
    book.save(path)
    return path


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "test.db") as s:
        s.create_schema()
        s.upsert_company("ACME", {
            "name": "Acme Ltd", "current_price": 100.0, "market_cap": 5000.0,
            "outstanding_shares": 50.0, "last_updated": "2026-01-01",
            "last_updated_quarter": "2026-4", "industry": "Widgets",
        })
        s.replace_financials("ACME", [
            ("2022-03-31", "profit-loss", "Operating Profit", 10.0),
            ("2023-03-31", "profit-loss", "Operating Profit", 20.0),
            ("2024-03-31", "profit-loss", "Operating Profit", 30.0),
            ("2023-03-31", "profit-loss", "EPS in Rs", 4.0),
            ("2024-03-31", "profit-loss", "EPS in Rs", 5.0),
            ("2024-03-31", "balance-sheet", "Reserves", 900.0),
            ("2024-03-31", "balance-sheet", "Borrowings", 250.0),
            ("2024-06-30", "quarters", "EPS in Rs", 1.0),
            ("2024-09-30", "quarters", "EPS in Rs", 2.0),
            ("2024-12-31", "quarters", "EPS in Rs", 3.0),
            ("2025-03-31", "quarters", "EPS in Rs", 4.0),
        ])
        s.replace_valuations("ACME", "derived", {"ebit": 77.0, "free_cash_flow": -5.0})
        yield s


def read(path, row, column):
    return load_workbook(path)[wb.DATA_SHEET].cell(row, column).value


class TestPopulate:
    def test_updates_an_existing_row_in_place(self, store, template, tmp_path):
        out = tmp_path / "out.xlsx"
        _, updated, appended = populate(store, template, out)
        assert (updated, appended) == (1, 0)
        assert read(out, 4, wb.COMPANY_NAME) == "Acme Ltd"

    def test_leaves_the_template_untouched(self, store, template, tmp_path):
        populate(store, template, tmp_path / "out.xlsx")
        assert read(template, 4, wb.COMPANY_NAME) is None

    def test_in_place_writes_to_the_template(self, store, template, tmp_path):
        populate(store, template, tmp_path / "unused.xlsx", in_place=True)
        assert read(template, 4, wb.COMPANY_NAME) == "Acme Ltd"

    def test_preserves_existing_formulas(self, store, template, tmp_path):
        # The workbook is the model; the scraper must not overwrite its maths.
        out = tmp_path / "out.xlsx"
        populate(store, template, out)
        assert read(out, 4, 10) == "=H4/I4"

    def test_appends_an_unknown_ticker_to_the_first_blank_row(self, store, template, tmp_path):
        store.upsert_company("NEWCO", {"name": "New Co", "last_updated": "2026-01-01"})
        out = tmp_path / "out.xlsx"
        _, updated, appended = populate(store, template, out)
        assert appended == 1
        assert read(out, 5, wb.TICKER_COLUMN) == "NEWCO"

    def test_missing_template_is_reported(self, store, tmp_path):
        with pytest.raises(WorkbookError, match="template not found"):
            populate(store, tmp_path / "nope.xlsx", tmp_path / "out.xlsx")

    def test_sheet_must_exist(self, store, tmp_path):
        book = Workbook()
        book.active.title = "Something Else"
        bad = tmp_path / "bad.xlsx"
        book.save(bad)
        with pytest.raises(WorkbookError, match="no 'Data' sheet"):
            populate(store, bad, tmp_path / "out.xlsx")


class TestSeriesLayout:
    def test_series_are_written_newest_first(self, store, template, tmp_path):
        out = tmp_path / "out.xlsx"
        populate(store, template, out)
        # CD is Y5, the latest year, because CP (current P/E) divides by it.
        assert read(out, 4, wb.EPS_YEARLY_FIRST) == 5.0
        assert read(out, 4, wb.EPS_YEARLY_FIRST + 1) == 4.0
        assert read(out, 4, wb.EBITDA_FIRST) == 30.0
        assert read(out, 4, wb.EBITDA_FIRST + 2) == 10.0

    def test_ttm_eps_sums_the_four_newest_quarters(self, store, template, tmp_path):
        out = tmp_path / "out.xlsx"
        populate(store, template, out)
        assert read(out, 4, wb.EPS_TTM) == 10.0  # 4 + 3 + 2 + 1

    def test_latest_balance_sheet_values_are_used(self, store, template, tmp_path):
        out = tmp_path / "out.xlsx"
        populate(store, template, out)
        assert read(out, 4, wb.RESERVES) == 900.0
        assert read(out, 4, wb.LONG_TERM_BORROWINGS) == 250.0

    def test_debt_column_carries_borrowings(self, store, template, tmp_path):
        # N is headed "CURRENT LIABILITY" but holds debt, so that
        # P=(M+N)/O is a real debt-to-equity ratio. See workbook.DEBT.
        out = tmp_path / "out.xlsx"
        populate(store, template, out)
        assert read(out, 4, wb.DEBT) == 250.0

    def test_current_assets_and_liabilities_are_never_written(self, store, template, tmp_path):
        # Screener has no current/non-current split; writing a proxy here would
        # silently corrupt the current ratio.
        out = tmp_path / "out.xlsx"
        populate(store, template, out)
        assert read(out, 4, wb.CURRENT_ASSETS) is None
        assert read(out, 4, wb.CURRENT_LIABILITY_2) is None

    def test_derived_values_land_in_their_columns(self, store, template, tmp_path):
        out = tmp_path / "out.xlsx"
        populate(store, template, out)
        assert read(out, 4, wb.EBIT) == 77.0
        assert read(out, 4, wb.FREE_CASHFLOW) == -5.0

    def test_absent_values_leave_the_cell_alone(self, store, template, tmp_path):
        # Better a blank than a -8888888 sentinel poisoning the averages.
        out = tmp_path / "out.xlsx"
        populate(store, template, out)
        assert read(out, 4, wb.INDUSTRY_PE) is None
