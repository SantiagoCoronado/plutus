"""Binance combined-stream consumer for crypto miniTicker quotes.

Connects to a single `wss://stream.binance.com:9443/stream?streams=...` socket
carrying `<pair>@miniTicker` streams. A miniTicker payload gives the last price
`c` and the 24h open `o`, from which we derive the day change %. The stream URL
is rebuilt (close + reconnect) whenever the subscribed set changes; connection
errors trigger exponential backoff with jitter.
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import UTC, datetime

import websockets

from app.core.logging import get_logger
from app.providers.binance import BinanceProvider
from app.quotes.publisher import publish_quote

log = get_logger(__name__)

BINANCE_WS_BASE = "wss://stream.binance.com:9443/stream"
MINI_TICKER_EVENT = "24hrMiniTicker"

BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 30.0
BACKOFF_JITTER = 0.3
RECHECK_S = 5.0  # ceiling on how long we wait before re-checking the subscription set


def resolve_pair(symbol: str, meta: dict | None) -> str:
    """Binance trading pair for a crypto asset. An explicit
    metadata provider_symbols.binance wins; otherwise fall back to the provider's
    canonical mapping (BTC -> BTCUSDT)."""
    provider_symbols = (meta or {}).get("provider_symbols", {})
    pair = provider_symbols.get("binance")
    return (pair or BinanceProvider._to_pair(symbol)).upper()


def build_stream_url(pairs) -> str:
    streams = "/".join(f"{pair.lower()}@miniTicker" for pair in sorted(pairs))
    return f"{BINANCE_WS_BASE}?streams={streams}"


def parse_message(raw: str, pair_to_symbol: dict[str, str]) -> dict | None:
    """Combined-stream miniTicker frame -> tick dict, or None when the frame is
    not a miniTicker or its pair isn't one we subscribed to. Pure — unit-tested."""
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(msg, dict):
        return None
    data = msg.get("data", msg)  # combined stream wraps in {stream, data}; tolerate raw
    if not isinstance(data, dict) or data.get("e") != MINI_TICKER_EVENT:
        return None
    pair = str(data.get("s", "")).upper()
    symbol = pair_to_symbol.get(pair)
    if symbol is None:
        return None
    try:
        close = float(data["c"])
        open_ = float(data["o"])
    except (KeyError, ValueError, TypeError):
        return None
    change_pct = (close - open_) / open_ * 100.0 if open_ else 0.0
    event_ms = data.get("E")
    ts = (
        datetime.fromtimestamp(event_ms / 1000, UTC).isoformat()
        if isinstance(event_ms, int | float)
        else datetime.now(UTC).isoformat()
    )
    return {
        "symbol": symbol,
        "asset_class": "crypto",
        "price": close,
        "change_pct": round(change_pct, 4),
        "ts": ts,
        "source": "binance",
    }


class BinanceStreamConsumer:
    """Owns one Binance websocket, resubscribing when `state.pairs()` changes."""

    def __init__(
        self,
        redis,
        state,
        *,
        stop: asyncio.Event | None = None,
        connect=websockets.connect,
        sleep=asyncio.sleep,
    ) -> None:
        self._redis = redis
        self._state = state
        self._stop = stop or asyncio.Event()
        self._connect = connect
        self._sleep = sleep

    async def handle_raw(self, raw: str) -> dict | None:
        """Parse one frame and, if it's a subscribed tick, publish it."""
        tick = parse_message(raw, self._state.crypto_pairs)
        if tick is not None:
            await publish_quote(self._redis, tick)
        return tick

    async def run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            pairs = self._state.pairs()
            if not pairs:
                await self._idle()
                continue
            url = build_stream_url(pairs)
            try:
                async with self._connect(url) as ws:
                    attempt = 0
                    log.info("binance_ws.connected", pairs=len(pairs))
                    await self._consume(ws, pairs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on any socket error
                if self._stop.is_set():
                    break
                delay = self._backoff(attempt)
                attempt += 1
                log.warning("binance_ws.reconnect", error=str(exc), delay=round(delay, 2))
                await self._sleep(delay)

    async def _consume(self, ws, pairs: set[str]) -> None:
        while not self._stop.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=RECHECK_S)
            except TimeoutError:
                if self._state.pairs() != pairs:
                    return  # set changed -> rebuild the URL
                continue
            await self.handle_raw(raw)
            if self._state.pairs() != pairs:
                return

    async def _idle(self) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=RECHECK_S)
        except TimeoutError:
            pass

    def _backoff(self, attempt: int) -> float:
        delay = min(BACKOFF_BASE_S * (2**attempt), BACKOFF_CAP_S)
        return delay * (1 + random.uniform(0, BACKOFF_JITTER))
