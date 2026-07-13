"""Browser-facing live-quote websocket: `WS /ws/quotes`.

Mounted on the raw app (NOT under api_router — the bearer Depends can't run for a
websocket handshake). Auth is a single-use short-TTL ticket: the browser calls
`POST /api/v1/ws-ticket` (bearer-authenticated) and connects with `?ticket=`,
so the long-lived APP_AUTH_TOKEN never rides in a URL where proxy logs and
browser history would capture it (spec phase 9 M4). After a
`{"action":"subscribe","symbols":[...]}` frame the server replays the current
last quotes for those symbols, then forwards matching ticks from the shared
Redis pub/sub channel, sending a heartbeat when idle.
"""

from __future__ import annotations

import asyncio
import json
import secrets

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.config import get_settings
from app.core.logging import get_logger
from app.quotes.publisher import CHANNEL, read_last_quotes

log = get_logger(__name__)

HEARTBEAT_S = 20
CLOSE_UNAUTHORIZED = 4401
TICKET_PREFIX = "ws:ticket:"
TICKET_TTL_S = 30

router = APIRouter(tags=["quotes"])


@router.post("/ws-ticket")
def mint_ws_ticket():
    """Single-use websocket ticket (30s TTL). Sits under the bearer-auth'd
    api_router; consuming it is GETDEL, so a replayed ticket is rejected."""
    from app.providers.registry import _shared_redis

    ticket = secrets.token_urlsafe(24)
    _shared_redis().setex(f"{TICKET_PREFIX}{ticket}", TICKET_TTL_S, "1")
    return {"ticket": ticket, "expires_in": TICKET_TTL_S}


async def _consume_ticket(redis, ticket: str) -> bool:
    if not ticket:
        return False
    return await redis.getdel(f"{TICKET_PREFIX}{ticket}") is not None


def _parse_subscribe(raw: str, current: set[str]) -> set[str]:
    """Return the new symbol set from a client frame, or `current` if malformed."""
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return current
    if isinstance(msg, dict) and msg.get("action") == "subscribe":
        symbols = msg.get("symbols")
        if isinstance(symbols, list):
            return {str(s).upper() for s in symbols}
    return current


async def _replay(websocket: WebSocket, redis, symbols: set[str]) -> None:
    if not symbols:
        return
    for tick in (await read_last_quotes(redis, symbols)).values():
        await websocket.send_json({"type": "tick", **tick})


async def quotes_ws(websocket: WebSocket) -> None:
    redis = aioredis.Redis.from_url(get_settings().redis_url, decode_responses=True)
    if not await _consume_ticket(redis, websocket.query_params.get("ticket", "")):
        await websocket.accept()
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        await redis.aclose()
        return
    await websocket.accept()
    pubsub = redis.pubsub()
    await pubsub.subscribe(CHANNEL)

    symbols: set[str] = set()
    client_task = asyncio.ensure_future(websocket.receive_text())
    pubsub_task = asyncio.ensure_future(
        pubsub.get_message(ignore_subscribe_messages=True, timeout=HEARTBEAT_S)
    )
    try:
        while True:
            done, _ = await asyncio.wait(
                {client_task, pubsub_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if client_task in done:
                raw = client_task.result()  # raises WebSocketDisconnect on close
                symbols = _parse_subscribe(raw, symbols)
                await _replay(websocket, redis, symbols)
                client_task = asyncio.ensure_future(websocket.receive_text())
            if pubsub_task in done:
                message = pubsub_task.result()
                if message is None:
                    await websocket.send_json({"type": "heartbeat"})
                else:
                    tick = json.loads(message["data"])
                    if str(tick.get("symbol", "")).upper() in symbols:
                        await websocket.send_json({"type": "tick", **tick})
                pubsub_task = asyncio.ensure_future(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=HEARTBEAT_S)
                )
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 - a broken client shouldn't spam logs
        log.info("ws_quotes.closed", error=str(exc))
    finally:
        client_task.cancel()
        pubsub_task.cancel()
        await pubsub.unsubscribe(CHANNEL)
        await pubsub.aclose()
        await redis.aclose()
