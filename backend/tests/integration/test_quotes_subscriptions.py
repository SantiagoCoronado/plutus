"""desired_symbols() union: market strip + watchlist items + open positions +
armed alerts, de-duplicated; closed positions and non-armed alerts excluded.
Needs the DB (JSONB metadata + the ledger aggregate), so it's an integration test.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.db import session_scope
from app.ingestion.seed import seed_assets
from app.models import Account, AlertRule, Asset, Transaction, Watchlist, WatchlistItem
from app.quotes.subscriptions import (
    MARKET_STRIP,
    desired_symbols,
    resolve_crypto_pairs,
)

pytestmark = pytest.mark.integration


def _asset(session, symbol, asset_class="stock"):
    asset = Asset(symbol=symbol, name=symbol, asset_class=asset_class, currency="USD")
    session.add(asset)
    session.flush()
    return asset.id


@pytest.fixture
def seeded():
    return {symbol: asset_id for asset_id, symbol in seed_assets()}


def test_market_strip_shape():
    # M6 dashboard consumes this constant; keep the (label, symbol, class) shape.
    symbols = {sym for _, sym, _ in MARKET_STRIP}
    assert symbols == {"SPY", "QQQ", "BTC", "ETH", "EURUSD", "USDMXN", "UUP"}
    assert all(len(row) == 3 for row in MARKET_STRIP)


def test_desired_symbols_union(seeded):
    with session_scope() as session:
        aapl = seeded["AAPL"]
        msft = _asset(session, "MSFT")
        tsla = _asset(session, "TSLA")
        nvda = _asset(session, "NVDA")
        amd = _asset(session, "AMD")

        # watchlist -> AAPL
        watchlist = session.scalar(select(Watchlist).where(Watchlist.name == "Default"))
        wl_id = watchlist.id if watchlist else _new_watchlist(session)
        session.add(WatchlistItem(watchlist_id=wl_id, asset_id=aapl))

        # armed alert -> MSFT ; triggered alert -> AMD (excluded)
        session.add(AlertRule(asset_id=msft, condition="above", threshold=100, status="armed"))
        session.add(AlertRule(asset_id=amd, condition="above", threshold=100, status="triggered"))

        # open position -> TSLA ; fully-closed position -> NVDA (excluded)
        account = Account(name="Broker", type="brokerage", currency="USD")
        session.add(account)
        session.flush()
        _buy(session, account.id, tsla, 5)
        _buy(session, account.id, nvda, 5)
        _sell(session, account.id, nvda, 5)
        session.flush()

        desired = desired_symbols(session)

    for _, symbol, asset_class in MARKET_STRIP:
        assert desired.get(symbol) == asset_class
    assert desired["AAPL"] == "stock"  # watchlist
    assert desired["MSFT"] == "stock"  # armed alert
    assert desired["TSLA"] == "stock"  # open position
    assert "NVDA" not in desired  # closed position
    assert "AMD" not in desired  # triggered (not armed) alert
    # dict keys are inherently unique -> no duplicates
    assert len(desired) == len(set(desired))


def test_resolve_crypto_pairs_from_metadata(seeded):
    with session_scope() as session:
        symbols = desired_symbols(session)
        pairs = resolve_crypto_pairs(session, symbols)
    assert pairs == {"BTCUSDT": "BTC", "ETHUSDT": "ETH"}


def _new_watchlist(session):
    wl = Watchlist(name="Quotes")
    session.add(wl)
    session.flush()
    return wl.id


def _buy(session, account_id, asset_id, quantity):
    session.add(
        Transaction(
            account_id=account_id,
            asset_id=asset_id,
            type="buy",
            ts=datetime(2026, 1, 2, tzinfo=UTC),
            quantity=quantity,
            price=10,
            fees=0,
            currency="USD",
        )
    )


def _sell(session, account_id, asset_id, quantity):
    session.add(
        Transaction(
            account_id=account_id,
            asset_id=asset_id,
            type="sell",
            ts=datetime(2026, 1, 3, tzinfo=UTC),
            quantity=quantity,
            price=12,
            fees=0,
            currency="USD",
        )
    )
