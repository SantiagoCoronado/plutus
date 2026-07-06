"""What the streamer should track, resolved from the DB.

desired_symbols() is the union of the always-on market strip, every watchlist
item, every asset with an open position, and every armed alert. The streamer
re-reads this every ~30s (short-lived sessions) and reconciles subscriptions.
"""

from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models import AlertRule, Asset, Transaction, WatchlistItem
from app.quotes.binance_ws import resolve_pair

# (label, symbol, asset_class) — the always-on strip (dashboard M6 consumes this
# same constant for its MarketStrip; keep the tuple shape stable).
MARKET_STRIP: list[tuple[str, str, str]] = [
    ("S&P 500", "SPY", "etf"),
    ("Nasdaq 100", "QQQ", "etf"),
    ("Bitcoin", "BTC", "crypto"),
    ("Ethereum", "ETH", "crypto"),
    ("EUR/USD", "EURUSD", "forex"),
    ("USD/MXN", "USDMXN", "forex"),
    ("Dollar index", "UUP", "etf"),
]

POLL_CLASSES = ("stock", "etf", "forex")

# signed quantity per transaction type — mirrors valuation.QUANTITY_SIGNS so an
# "open position" here means the same thing it does on the portfolio page.
_POSITION_SIGN = case(
    (Transaction.type.in_(("buy", "transfer_in")), Transaction.quantity),
    (Transaction.type.in_(("sell", "transfer_out")), -Transaction.quantity),
    else_=0,
)


def desired_symbols(session: Session) -> dict[str, str]:
    """symbol -> asset_class for everything the streamer should track."""
    desired: dict[str, str] = {symbol: cls for _, symbol, cls in MARKET_STRIP}

    asset_ids: set[int] = set()
    asset_ids.update(session.scalars(select(WatchlistItem.asset_id)).all())
    asset_ids.update(
        session.scalars(select(AlertRule.asset_id).where(AlertRule.status == "armed")).all()
    )
    asset_ids.update(_open_position_asset_ids(session))

    if asset_ids:
        rows = session.execute(
            select(Asset.symbol, Asset.asset_class).where(
                Asset.id.in_(asset_ids), Asset.is_active.is_(True)
            )
        ).all()
        for symbol, asset_class in rows:
            desired.setdefault(symbol, asset_class)
    return desired


def _open_position_asset_ids(session: Session) -> set[int]:
    """Assets with a net-positive holding. A cheap SQL aggregate over the ledger —
    deliberately NOT compute_positions(), which loads every transaction and builds
    lots + FX + valuations on each ~30s reconcile (far too heavy just to learn
    which symbols to subscribe to)."""
    rows = session.execute(
        select(Transaction.asset_id)
        .where(Transaction.asset_id.is_not(None))
        .group_by(Transaction.asset_id)
        .having(func.coalesce(func.sum(_POSITION_SIGN), 0) > 0)
    ).all()
    return {row[0] for row in rows}


def resolve_crypto_pairs(session: Session, symbols: dict[str, str]) -> dict[str, str]:
    """binance pair -> our symbol, for the crypto members of `symbols`."""
    crypto = [sym for sym, cls in symbols.items() if cls == "crypto"]
    if not crypto:
        return {}
    metas = {
        asset.symbol: asset.meta
        for asset in session.scalars(
            select(Asset).where(Asset.symbol.in_(crypto), Asset.asset_class == "crypto")
        )
    }
    return {resolve_pair(sym, metas.get(sym)): sym for sym in crypto}


def read_state() -> tuple[dict[str, str], dict[str, str]]:
    """Short-lived DB read of the desired subscription set: (symbols, crypto_pairs)."""
    with SessionLocal() as session:
        symbols = desired_symbols(session)
        pairs = resolve_crypto_pairs(session, symbols)
    return symbols, pairs


class SubscriptionState:
    """Shared, mutable view of what to stream. The reconciler writes; the Binance
    consumer and poller read. Single event loop — no locking needed."""

    def __init__(self) -> None:
        self.symbols: dict[str, str] = {}
        self.crypto_pairs: dict[str, str] = {}  # binance pair -> our symbol

    def update(self, symbols: dict[str, str], crypto_pairs: dict[str, str]) -> None:
        self.symbols = symbols
        self.crypto_pairs = crypto_pairs

    def pairs(self) -> set[str]:
        return set(self.crypto_pairs)

    def poll_symbols(self) -> list[tuple[str, str]]:
        return [(s, c) for s, c in self.symbols.items() if c in POLL_CLASSES]
