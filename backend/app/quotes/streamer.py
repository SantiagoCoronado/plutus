"""Live-quote streamer entry point (`python -m app.quotes.streamer`).

Runs three cooperating tasks under a TaskGroup: a subscription reconciler that
re-reads the desired symbol set from the DB every ~30s, the Binance websocket
consumer (crypto), and the polling loop (stocks/etf/forex). Shuts down cleanly
on SIGTERM/SIGINT.
"""

from __future__ import annotations

import asyncio
import signal

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.quotes.binance_ws import BinanceStreamConsumer
from app.quotes.poller import PollLoop
from app.quotes.subscriptions import SubscriptionState, read_state

log = get_logger(__name__)

RECONCILE_INTERVAL_S = 30
# liveness beacon: refreshed every reconcile tick, read by the compose
# healthcheck and the hourly watchdog. TTL > 2 ticks so one slow cycle
# doesn't flap the container unhealthy.
HEARTBEAT_KEY = "quotes:heartbeat"
HEARTBEAT_TTL_S = 120


async def _reconcile_loop(redis, state: SubscriptionState, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            symbols, pairs = await asyncio.to_thread(read_state)
            state.update(symbols, pairs)
            await redis.setex(HEARTBEAT_KEY, HEARTBEAT_TTL_S, "1")
            log.info("quotes.reconciled", symbols=len(symbols), pairs=len(pairs))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("quotes.reconcile_error", error=str(exc))
        try:
            await asyncio.wait_for(stop.wait(), timeout=RECONCILE_INTERVAL_S)
        except TimeoutError:
            pass


async def run() -> None:
    settings = get_settings()
    redis = aioredis.Redis.from_url(settings.redis_url, decode_responses=True)
    state = SubscriptionState()
    stop = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - non-unix
            pass

    # prime once so the first poll/stream cycle already has a subscription set
    try:
        symbols, pairs = await asyncio.to_thread(read_state)
        state.update(symbols, pairs)
    except Exception as exc:  # noqa: BLE001
        log.warning("quotes.prime_error", error=str(exc))

    consumer = BinanceStreamConsumer(redis, state, stop=stop)
    poller = PollLoop(redis, state, stop=stop)

    log.info("quotes.streamer_start")
    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(_reconcile_loop(redis, state, stop))
            tg.create_task(consumer.run())
            tg.create_task(poller.run())
            await stop.wait()
    finally:
        await redis.aclose()
        log.info("quotes.streamer_stop")


def main() -> None:
    configure_logging()
    asyncio.run(run())


if __name__ == "__main__":
    main()
