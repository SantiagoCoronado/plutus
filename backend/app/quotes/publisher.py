"""Redis fan-out for live ticks: publish to a pub/sub channel (for connected
websockets) and stash the latest per-asset quote under a short-TTL key (for
instant replay on connect and for the M4 alert evaluator).

A tick is a plain dict: {symbol, asset_class, price, change_pct, ts, source}.
Last-quote keys are namespaced by asset class (`quote:last:<class>:<SYMBOL>`)
so a stock and a crypto sharing a ticker can never overwrite each other — the
alert evaluator reads the exact (class, symbol) bucket.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

CHANNEL = "quotes:ticks"
LAST_PREFIX = "quote:last:"
LAST_TTL_S = 120

# every class a tick can carry; the symbol-only reader scans these buckets
ASSET_CLASSES = ("stock", "etf", "crypto", "forex")


def _last_key(asset_class: str, symbol: str) -> str:
    return f"{LAST_PREFIX}{asset_class}:{symbol.upper()}"


async def publish_quote(redis, tick: dict[str, Any]) -> None:
    """Publish one tick to the pub/sub channel and refresh its last-quote key."""
    payload = json.dumps(tick)
    await redis.publish(CHANNEL, payload)
    await redis.setex(_last_key(tick["asset_class"], tick["symbol"]), LAST_TTL_S, payload)


async def read_last_quotes(redis, symbols: Iterable[str]) -> dict[str, dict]:
    """Latest stored tick per symbol across every class bucket (websocket replay
    is symbol-addressed). Expired / never-published symbols are skipped."""
    symbols = [s for s in symbols]
    if not symbols:
        return {}
    keys = [_last_key(cls, s) for s in symbols for cls in ASSET_CLASSES]
    values = await redis.mget(keys)
    out: dict[str, dict] = {}
    for raw in values:
        if raw is not None:
            tick = json.loads(raw)
            out[str(tick["symbol"]).upper()] = tick
    return out


def read_last_quotes_by_class_sync(
    redis, pairs: Iterable[tuple[str, str]]
) -> dict[tuple[str, str], dict]:
    """(asset_class, symbol) -> latest tick, exact-bucket lookup for the alert
    evaluator (sync redis client — it runs as a per-minute Celery beat task)."""
    pairs = [(cls, sym.upper()) for cls, sym in pairs]
    if not pairs:
        return {}
    values = redis.mget([_last_key(cls, sym) for cls, sym in pairs])
    out: dict[tuple[str, str], dict] = {}
    for (cls, sym), raw in zip(pairs, values, strict=True):
        if raw is not None:
            out[(cls, sym)] = json.loads(raw)
    return out
