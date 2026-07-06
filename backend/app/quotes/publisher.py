"""Redis fan-out for live ticks: publish to a pub/sub channel (for connected
websockets) and stash the latest per-symbol quote under a short-TTL key (for
instant replay on connect and for the M4 alert evaluator).

A tick is a plain dict: {symbol, price, change_pct, ts, source}.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

CHANNEL = "quotes:ticks"
LAST_PREFIX = "quote:last:"
LAST_TTL_S = 120


def _last_key(symbol: str) -> str:
    return f"{LAST_PREFIX}{symbol.upper()}"


async def publish_quote(redis, tick: dict[str, Any]) -> None:
    """Publish one tick to the pub/sub channel and refresh its last-quote key."""
    payload = json.dumps(tick)
    await redis.publish(CHANNEL, payload)
    await redis.setex(_last_key(tick["symbol"]), LAST_TTL_S, payload)


async def read_last_quotes(redis, symbols: Iterable[str]) -> dict[str, dict]:
    """Latest stored tick per symbol (uppercased key), skipping any that have
    expired or never published. Used for websocket replay + alert evaluation."""
    symbols = [s for s in symbols]
    if not symbols:
        return {}
    values = await redis.mget([_last_key(s) for s in symbols])
    out: dict[str, dict] = {}
    for symbol, raw in zip(symbols, values, strict=True):
        if raw is not None:
            out[symbol.upper()] = json.loads(raw)
    return out
