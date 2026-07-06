"""Live-quote streamer (Phase 7 M3): a long-running asyncio process that fans
Binance websocket ticks (crypto) + polled quotes (stocks/etf/forex) out to the
browser over one FastAPI websocket via Redis pub/sub.

Intraday prices live ONLY in Redis (channel + short-TTL keys). Nothing in this
package imports the daily-bars ORM model or writes price rows — that guarantee is
enforced by tests/unit/test_quotes_no_ohlcv.py.
"""
