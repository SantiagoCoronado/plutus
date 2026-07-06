"""Bitso sync normalization + marker pagination, exercised without a database.

Normalization is asserted to match the CSV `bitso` preset's output for the
equivalent row, so API sync and CSV import dedupe against each other.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.exchanges.base import ExchangeTrade
from app.exchanges.bitso import PAGE_LIMIT
from app.exchanges.sync import (
    _resolve_asset,
    _sync_trades,
    _trade_record,
    _transfer_record,
)
from app.models import Asset
from app.portfolio.csv_import import PRESETS, _row_to_record


def _asset(symbol: str, asset_class: str = "crypto", asset_id: int = 1) -> Asset:
    asset = Asset(symbol=symbol, name=symbol, asset_class=asset_class)
    asset.id = asset_id
    return asset


def _assets(*assets: Asset) -> dict[str, list[Asset]]:
    lookup: dict[str, list[Asset]] = {}
    for asset in assets:
        lookup.setdefault(asset.symbol.upper(), []).append(asset)
    return lookup


TS = datetime(2026, 4, 8, 17, 52, 31, tzinfo=UTC)


class TestTradeNormalization:
    def test_matches_csv_preset_output(self):
        assets = _assets(_asset("BTC", asset_id=7))
        unknown: set[str] = set()

        trade = ExchangeTrade(
            tid="51756",
            book="btc_mxn",
            side="sell",
            major=-0.25232073,
            minor=1014.10,
            price=4019.51,
            fees_amount=-10.24,
            fees_currency="mxn",
            created_at=TS,
        )
        api_record = _trade_record(1, trade, assets, unknown)

        # the equivalent Bitso CSV export row through the shipped preset
        csv_raw = {
            "tid": "51756",
            "date": "2026-04-08T17:52:31+00:00",
            "type": "sell",
            "major": "btc",   # CSV export: 'major' column holds the asset symbol
            "minor": "mxn",   # 'minor' column holds the currency
            "amount": "0.25232073",
            "rate": "4019.51",
            "fee": "10.24",
        }
        csv_lookup = _asset_lookup_from(assets)
        csv_record = _row_to_record(
            csv_raw, PRESETS["bitso"]["mapping"], csv_lookup, ZoneInfo("America/Mexico_City"), 1
        )

        for field in ("asset_id", "type", "currency", "external_id"):
            assert api_record[field] == csv_record[field]
        assert api_record["quantity"] == pytest.approx(csv_record["quantity"])
        assert api_record["price"] == pytest.approx(csv_record["price"])
        assert api_record["fees"] == pytest.approx(csv_record["fees"])
        assert api_record["type"] == "sell"
        assert api_record["currency"] == "MXN"
        assert api_record["external_id"] == "51756"

    def test_unknown_symbol_is_skipped_and_reported(self):
        assets = _assets(_asset("BTC"))
        unknown: set[str] = set()
        trade = ExchangeTrade(
            tid="1", book="doge_mxn", side="buy", major=10.0, minor=1.0,
            price=0.1, fees_amount=0.0, fees_currency="mxn", created_at=TS,
        )
        assert _trade_record(1, trade, assets, unknown) is None
        assert unknown == {"DOGE"}


class TestTransferNormalization:
    def test_fiat_funding_is_a_deposit(self):
        assets = _assets(_asset("BTC"))
        unknown: set[str] = set()
        record = _transfer_record(
            1, "MXN", 5000.0, "fid-1", "deposit", "transfer_in", TS, "spei", assets, unknown
        )
        assert record["type"] == "deposit"
        assert record["asset_id"] is None
        assert record["currency"] == "MXN"
        assert record["quantity"] == pytest.approx(5000.0)
        assert record["external_id"] == "fid-1"
        assert unknown == set()

    def test_crypto_funding_is_a_transfer_in(self):
        assets = _assets(_asset("BTC", asset_id=7))
        unknown: set[str] = set()
        record = _transfer_record(
            1, "BTC", 0.25, "fid-2", "deposit", "transfer_in", TS, None, assets, unknown
        )
        assert record["type"] == "transfer_in"
        assert record["asset_id"] == 7
        assert record["currency"] == "BTC"
        assert record["external_id"] == "fid-2"

    def test_crypto_withdrawal_is_a_transfer_out(self):
        assets = _assets(_asset("BTC", asset_id=7))
        record = _transfer_record(
            1, "BTC", 0.1, "wid-1", "withdrawal", "transfer_out", TS, None, assets, set()
        )
        assert record["type"] == "transfer_out"
        assert record["asset_id"] == 7

    def test_unknown_crypto_transfer_is_skipped_and_reported(self):
        unknown: set[str] = set()
        record = _transfer_record(
            1, "SOL", 3.0, "fid-3", "deposit", "transfer_in", TS, None, _assets(), unknown
        )
        assert record is None
        assert unknown == {"SOL"}


class TestResolveAsset:
    def test_prefers_crypto_when_symbol_is_ambiguous(self):
        crypto = _asset("BTC", "crypto", 1)
        stock = _asset("BTC", "stock", 2)
        assert _resolve_asset(_assets(crypto, stock), "BTC") is crypto

    def test_unknown_symbol_returns_none(self):
        assert _resolve_asset(_assets(), "NOPE") is None


class _FakeResult:
    def first(self):
        return (1,)  # every insert is a fresh row for this fake


class _FakeSession:
    def __init__(self):
        self.commits = 0
        self.executed = 0

    def execute(self, _stmt):
        self.executed += 1
        return _FakeResult()

    def commit(self):
        self.commits += 1


class _FakeLink:
    last_trade_tid = None


class _FakeClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.markers: list[str | None] = []

    def fetch_trades(self, since_tid=None):
        self.markers.append(since_tid)
        return self.pages.pop(0) if self.pages else []


def _trade(i: int) -> ExchangeTrade:
    return ExchangeTrade(
        tid=str(i), book="btc_mxn", side="buy", major=1.0, minor=1.0,
        price=1.0, fees_amount=0.0, fees_currency="mxn", created_at=TS,
    )


class TestPagination:
    def test_stitches_pages_and_advances_marker(self):
        assets = _assets(_asset("BTC", asset_id=7))
        page1 = [_trade(i) for i in range(PAGE_LIMIT)]
        page2 = [_trade(i) for i in range(PAGE_LIMIT, PAGE_LIMIT + 3)]
        client = _FakeClient([page1, page2])
        session = _FakeSession()
        link = _FakeLink()

        created, skipped, pages = _sync_trades(session, 1, link, client, assets, set())

        assert created == PAGE_LIMIT + 3
        assert skipped == 0
        assert pages == 2
        # first request has no marker; second resumes from the last tid of page 1
        assert client.markers == [None, str(PAGE_LIMIT - 1)]
        assert link.last_trade_tid == str(PAGE_LIMIT + 2)
        assert session.commits == 2


def _asset_lookup_from(assets: dict[str, list[Asset]]):
    # csv_import._asset_lookup reads a session; reuse the same shape it produces
    return assets
