"""Polling loop for stock/etf/forex quotes.

Every 15s, for each subscribed non-crypto symbol, it calls the configured
provider's sync get_quote via asyncio.to_thread (the provider clients are sync by
design and already cache quotes for 60s). change_pct is measured against the
latest stored daily close — read with raw SQL so this package never touches the
daily-bars ORM model (see the import-guard test). Any error is logged and the
loop continues; a provider without a key is skipped quietly.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, text

from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.models import Asset
from app.providers.base import ProviderError, ProviderNotConfigured
from app.providers.registry import get_provider
from app.quotes.market_hours import forex_open, us_market_open
from app.quotes.publisher import publish_quote

log = get_logger(__name__)

POLL_INTERVAL_S = 15

# latest stored daily close (the previous trading day, during market hours).
# Raw SQL by design: this package must not touch the daily-bars ORM model.
_LATEST_CLOSE_SQL = text(
    "SELECT close FROM ohlcv "
    "WHERE asset_id = :asset_id AND interval = '1d' AND ts < :cutoff "
    "ORDER BY ts DESC LIMIT 1"
)


class PollLoop:
    def __init__(
        self,
        redis,
        state,
        *,
        stop: asyncio.Event | None = None,
        interval: float = POLL_INTERVAL_S,
        now=None,
    ) -> None:
        self._redis = redis
        self._state = state
        self._stop = stop or asyncio.Event()
        self._interval = interval
        self._now = now or (lambda: datetime.now(UTC))

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a cycle must never kill the loop
                log.warning("poller.cycle_error", error=str(exc))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except TimeoutError:
                pass

    async def _poll_once(self) -> None:
        now = self._now()
        for symbol, asset_class in self._state.poll_symbols():
            if asset_class in ("stock", "etf") and not us_market_open(now):
                continue
            if asset_class == "forex" and not forex_open(now):
                continue
            try:
                tick = await asyncio.to_thread(self._fetch, symbol, asset_class)
            except ProviderNotConfigured:
                continue  # no API key for this class -> skip quietly
            except ProviderError as exc:
                log.warning("poller.quote_error", symbol=symbol, error=str(exc))
                continue
            except Exception as exc:  # noqa: BLE001
                log.warning("poller.error", symbol=symbol, error=str(exc))
                continue
            if tick is not None:
                await publish_quote(self._redis, tick)

    def _fetch(self, symbol: str, asset_class: str) -> dict | None:
        """Blocking: resolve the provider symbol, fetch the quote, compute the
        day change vs the last daily close. Runs in a worker thread."""
        provider = get_provider(asset_class)
        with SessionLocal() as session:
            asset = session.scalar(
                select(Asset).where(Asset.symbol == symbol, Asset.asset_class == asset_class)
            )
            if asset is None:
                return None
            provider_symbol = asset.provider_symbol_map.get(provider.name, symbol)
            prev_close = self._previous_close(session, asset.id)
        quote = provider.get_quote(provider_symbol, asset_class)
        change_pct = (quote.price - prev_close) / prev_close * 100.0 if prev_close else 0.0
        return {
            "symbol": symbol,
            "asset_class": asset_class,
            "price": quote.price,
            "change_pct": round(change_pct, 4),
            "ts": datetime.now(UTC).isoformat(),
            "source": provider.name,
        }

    @staticmethod
    def _previous_close(session, asset_id: int) -> float | None:
        cutoff = datetime.combine(date.today() + timedelta(days=1), datetime.min.time(), UTC)
        row = session.execute(_LATEST_CLOSE_SQL, {"asset_id": asset_id, "cutoff": cutoff}).first()
        return float(row[0]) if row and row[0] is not None else None
