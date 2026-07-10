"""CSV importer: sniffing, mapping suggestion, preset detection, row parsing."""

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.portfolio.csv_import import (
    RowError,
    _content_hash,
    _read_csv,
    _row_to_record,
    parse_preview,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


class FakeAsset:
    def __init__(self, id, symbol, asset_class):
        self.id = id
        self.symbol = symbol
        self.asset_class = asset_class


ASSETS = {
    "BTC": [FakeAsset(2, "BTC", "crypto")],
    "AAPL": [FakeAsset(1, "AAPL", "stock")],
    "DUP": [FakeAsset(8, "DUP", "stock"), FakeAsset(9, "DUP", "etf")],
}
ZONE = ZoneInfo("America/Mexico_City")

BITSO_CSV = (
    "tid,date,type,major,minor,amount,rate,value,fee\n"
    "1001,2026-03-02 10:15,buy,btc,mxn,0.05,1800000,90000,225\n"
    "1002,2026-03-05 16:40,sell,btc,mxn,0.01,1900000,19000,47.5\n"
)


class TestPreview:
    def test_bitso_preset_detected_and_mapped(self):
        preview = parse_preview(BITSO_CSV)
        assert preview.preset == "bitso"
        assert preview.row_count == 2
        assert preview.suggested_mapping["symbol"] == "major"
        assert preview.suggested_mapping["external_id"] == "tid"

    def test_generic_headers_get_fuzzy_suggestions(self):
        content = (
            "Fecha,Tipo,Ticker,Cantidad,Precio,Comisión,Divisa\n"
            "2026-01-02,compra,AAPL,3,200,1,USD\n"
        )
        preview = parse_preview(content)
        assert preview.preset is None
        assert preview.suggested_mapping["ts"] == "Fecha"
        assert preview.suggested_mapping["type"] == "Tipo"
        assert preview.suggested_mapping["symbol"] == "Ticker"
        assert preview.suggested_mapping["quantity"] == "Cantidad"
        assert preview.suggested_mapping["price"] == "Precio"
        assert preview.suggested_mapping["fees"] == "Comisión"
        assert preview.suggested_mapping["currency"] == "Divisa"

    def test_semicolon_delimiter_sniffed(self):
        content = "date;type;amount\n2026-01-02;deposit;100\n"
        preview = parse_preview(content)
        assert preview.columns == ["date", "type", "amount"]

    def test_book_column_supersedes_symbol_and_currency(self):
        content = "date,type,book,amount,price\n2026-01-02,buy,btc_mxn,0.1,1800000\n"
        preview = parse_preview(content)
        assert preview.suggested_mapping.get("book") == "book"
        assert "symbol" not in preview.suggested_mapping

    def test_blank_lines_ignored(self):
        assert parse_preview(BITSO_CSV + "\n\n").row_count == 2


MAPPING = {
    "external_id": "tid",
    "ts": "date",
    "type": "type",
    "symbol": "major",
    "currency": "minor",
    "quantity": "amount",
    "price": "rate",
    "fees": "fee",
}


def row(**overrides) -> dict:
    base = {
        "tid": "1001",
        "date": "2026-03-02 10:15",
        "type": "buy",
        "major": "btc",
        "minor": "mxn",
        "amount": "0.05",
        "rate": "1800000",
        "fee": "225",
    }
    base.update(overrides)
    return base


class TestRowParsing:
    def test_happy_path(self):
        record = _row_to_record(row(), MAPPING, ASSETS, ZONE, account_id=7)
        assert record["asset_id"] == 2
        assert record["type"] == "buy"
        assert record["currency"] == "MXN"
        assert record["quantity"] == pytest.approx(0.05)
        assert record["price"] == pytest.approx(1_800_000)
        assert record["external_id"] == "1001"
        # naive timestamps localize to the requested zone
        assert record["ts"].utcoffset() is not None

    def test_spanish_type_synonyms(self):
        record = _row_to_record(row(type="venta"), MAPPING, ASSETS, ZONE, 7)
        assert record["type"] == "sell"

    def test_unknown_type_and_symbol_and_bad_number(self):
        with pytest.raises(RowError, match="unrecognized transaction type"):
            _row_to_record(row(type="stake"), MAPPING, ASSETS, ZONE, 7)
        with pytest.raises(RowError, match="unknown symbol"):
            _row_to_record(row(major="doge"), MAPPING, ASSETS, ZONE, 7)
        with pytest.raises(RowError, match="not a number"):
            _row_to_record(row(amount="lots"), MAPPING, ASSETS, ZONE, 7)

    def test_ambiguous_symbol(self):
        with pytest.raises(RowError, match="ambiguous"):
            _row_to_record(row(major="dup"), MAPPING, ASSETS, ZONE, 7)

    def test_buy_needs_price(self):
        with pytest.raises(RowError, match="need a price"):
            _row_to_record(row(rate=""), MAPPING, ASSETS, ZONE, 7)

    def test_deposit_needs_no_asset(self):
        record = _row_to_record(
            row(type="funding", major="", rate=""), MAPPING, ASSETS, ZONE, 7
        )
        assert record["type"] == "deposit"
        assert record["asset_id"] is None

    def test_missing_external_id_hashes_content(self):
        mapping = {k: v for k, v in MAPPING.items() if k != "external_id"}
        a = _row_to_record(row(), mapping, ASSETS, ZONE, 7)
        b = _row_to_record(row(), mapping, ASSETS, ZONE, 7)
        c = _row_to_record(row(amount="0.06"), mapping, ASSETS, ZONE, 7)
        assert a["external_id"] == b["external_id"]  # stable
        assert a["external_id"] != c["external_id"]  # content-sensitive

    def test_number_cleaning(self):
        record = _row_to_record(
            row(rate="1,800,000", fee="$225"), MAPPING, ASSETS, ZONE, 7
        )
        assert record["price"] == pytest.approx(1_800_000)
        assert record["fees"] == pytest.approx(225)


class TestSpanishLocale:
    def test_comma_decimal_numbers(self):
        record = _row_to_record(
            row(amount="1,5", rate="1.234,56", fee="0,25"),
            MAPPING,
            ASSETS,
            ZONE,
            7,
            number_format="1.234,56",
        )
        assert record["quantity"] == pytest.approx(1.5)
        assert record["price"] == pytest.approx(1234.56)
        assert record["fees"] == pytest.approx(0.25)

    def test_dot_decimal_default_unchanged(self):
        record = _row_to_record(row(rate="1,800,000.25"), MAPPING, ASSETS, ZONE, 7)
        assert record["price"] == pytest.approx(1_800_000.25)

    def test_dayfirst_and_monthfirst_pin_the_order(self):
        dayfirst = _row_to_record(
            row(date="02/03/2025 10:15"), MAPPING, ASSETS, ZONE, 7, date_order="dayfirst"
        )
        assert (dayfirst["ts"].month, dayfirst["ts"].day) == (3, 2)
        monthfirst = _row_to_record(
            row(date="02/03/2025 10:15"), MAPPING, ASSETS, ZONE, 7, date_order="monthfirst"
        )
        assert (monthfirst["ts"].month, monthfirst["ts"].day) == (2, 3)

    def test_ambiguous_date_under_auto_is_a_row_error(self):
        with pytest.raises(RowError, match="ambiguous.*date_order"):
            _row_to_record(row(date="02/03/2025 10:15"), MAPPING, ASSETS, ZONE, 7)

    def test_unambiguous_dates_parse_under_auto(self):
        # day > 12 forces one reading; year-first is never ambiguous
        record = _row_to_record(row(date="13/02/2025"), MAPPING, ASSETS, ZONE, 7)
        assert (record["ts"].month, record["ts"].day) == (2, 13)
        record = _row_to_record(row(date="2026-03-02 10:15"), MAPPING, ASSETS, ZONE, 7)
        assert (record["ts"].month, record["ts"].day) == (3, 2)

    def test_year_first_dates_ignore_date_order(self):
        record = _row_to_record(
            row(date="2026-03-02 10:15"), MAPPING, ASSETS, ZONE, 7, date_order="dayfirst"
        )
        assert (record["ts"].month, record["ts"].day) == (3, 2)

    def test_sniffed_suggestions_in_preview(self):
        content = (
            "fecha,tipo,instrumento,cantidad,precio,divisa\n"
            '13/03/2025,compra,BTC,"1,5","1.234,56",MXN\n'
        )
        preview = parse_preview(content)
        assert preview.suggested_number_format == "1.234,56"
        assert preview.suggested_date_order == "dayfirst"

    def test_dot_decimal_iso_file_sniffs_defaults(self):
        content = "date,type,symbol,amount,price\n2026-01-02,buy,BTC,1.5,200.25\n"
        preview = parse_preview(content)
        assert preview.suggested_number_format == "1,234.56"
        assert preview.suggested_date_order == "auto"

    def test_bitso_preset_pins_locale(self):
        preview = parse_preview(BITSO_CSV)
        assert preview.suggested_number_format == "1,234.56"
        assert preview.suggested_date_order == "auto"

    def test_golden_decimal_comma_dayfirst_fixture(self):
        content = (FIXTURES / "spanish_locale.csv").read_text()
        preview = parse_preview(content)
        assert preview.preset is None
        assert preview.suggested_number_format == "1.234,56"
        assert preview.suggested_date_order == "dayfirst"
        assert preview.suggested_mapping["ts"] == "fecha"
        assert preview.suggested_mapping["quantity"] == "cantidad"

        rows, _ = _read_csv(content)
        records = [
            _row_to_record(
                raw,
                preview.suggested_mapping,
                ASSETS,
                ZONE,
                7,
                number_format=preview.suggested_number_format,
                date_order=preview.suggested_date_order,
            )
            for raw in rows
        ]
        first, second = records
        assert first["type"] == "buy"
        assert first["quantity"] == pytest.approx(1.5)
        assert first["price"] == pytest.approx(1234.56)
        assert first["fees"] == pytest.approx(0.5)
        assert (first["ts"].year, first["ts"].month, first["ts"].day) == (2025, 3, 2)
        assert second["type"] == "sell"
        assert second["price"] == pytest.approx(2000.0)
        assert (second["ts"].month, second["ts"].day) == (3, 13)


def test_content_hash_is_deterministic():
    assert _content_hash(1, "a", None) == _content_hash(1, "a", None)
    assert _content_hash(1, "a", None) != _content_hash(2, "a", None)
    assert len(_content_hash("x")) == 32
