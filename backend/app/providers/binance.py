import time
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from app.providers.base import (
    CANDLE_COLUMNS,
    TTL_DAILY_BARS,
    TTL_QUOTE,
    ProviderError,
    empty_candles,
)
from app.providers.http import RateLimitedClient
from app.schemas.common import AssetClass, Interval, Quote, SymbolInfo

# Official public market-data-only host (no exchange endpoints). NOTE: like the main
# API it returns HTTP 451 from US IPs — fine from Mexico; KrakenProvider is the
# documented fallback if hosting ever relocates (with a hard ~720-daily-bar ceiling).
BASE_URL = "https://data-api.binance.vision"

KNOWN_QUOTE_SUFFIXES = ("USDT", "USDC", "FDUSD", "TUSD", "BTC", "ETH", "BNB", "EUR")

DAY_MS = 86_400_000


class BinanceProvider:
    """Real crypto daily OHLCV (replaces CoinGecko's synthetic candles). Keyless.

    Search/metadata stay on CoinGecko via the registry's SEARCH_DELEGATES —
    Binance has no name search. HTTP 418 = temp IP ban after ignored 429s; the
    client's ProviderError on it is correct behavior (stop, don't retry).
    """

    name = "binance"

    def __init__(self, client: RateLimitedClient) -> None:
        self._client = client

    @staticmethod
    def _to_pair(symbol: str) -> str:
        s = symbol.upper().replace("/", "")
        for suffix in KNOWN_QUOTE_SUFFIXES:
            # len check: a bare base symbol like "BTC" is not itself a pair
            if s.endswith(suffix) and len(s) > len(suffix):
                return s
        return s + "USDT"  # canonical 'BTC' works without provider_symbols meta

    def get_ohlcv(
        self, symbol: str, asset_class: AssetClass, interval: Interval, start: date, end: date
    ) -> pd.DataFrame:
        if interval != Interval.d1:
            raise ProviderError(f"binance adapter supports only 1d in Phase 2, got {interval}")
        pair = self._to_pair(symbol)
        start_ms = int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp() * 1000)
        end_dt = datetime(end.year, end.month, end.day, tzinfo=UTC) + timedelta(days=1)
        end_ms = int(end_dt.timestamp() * 1000) - 1

        klines: list[list] = []
        cursor = start_ms
        while cursor <= end_ms:
            batch = self._client.get_json(
                "/api/v3/klines",
                {
                    "symbol": pair,
                    "interval": "1d",
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": 1000,
                },
                cache_ttl=TTL_DAILY_BARS,
            )
            if not batch:
                break
            klines.extend(batch)
            if len(batch) < 1000:
                break
            cursor = batch[-1][0] + DAY_MS
        return self._parse_ohlcv(klines)

    @staticmethod
    def _parse_ohlcv(klines: list[list], now_ms: float | None = None) -> pd.DataFrame:
        if not klines:
            return empty_candles()
        if now_ms is None:
            now_ms = time.time() * 1000
        # kline: [openTime, open, high, low, close, volume, closeTime, ...]
        # drop the trailing in-progress bar — a partial candle poisons indicators/upserts
        if klines and float(klines[-1][6]) > now_ms:
            klines = klines[:-1]
        if not klines:
            return empty_candles()
        df = pd.DataFrame(
            [k[:6] for k in klines],
            columns=["open_ms", "open", "high", "low", "close", "volume"],
        )
        out = pd.DataFrame(
            {
                "ts": pd.to_datetime(df["open_ms"], unit="ms", utc=True).dt.normalize(),
                "open": df["open"].astype(float),
                "high": df["high"].astype(float),
                "low": df["low"].astype(float),
                "close": df["close"].astype(float),
                "volume": df["volume"].astype(float),
            }
        )
        return out.sort_values("ts").reset_index(drop=True)[CANDLE_COLUMNS]

    def get_quote(self, symbol: str, asset_class: AssetClass) -> Quote:
        pair = self._to_pair(symbol)
        payload = self._client.get_json(
            "/api/v3/ticker/price",
            {"symbol": pair},
            cache_ttl=TTL_QUOTE,
            acquire_timeout=3.0,
        )
        if "price" not in payload:
            raise ProviderError(f"binance: no quote for {pair}")
        return Quote(symbol=symbol, price=float(payload["price"]), currency="USD")

    def search_symbols(self, query: str) -> list[SymbolInfo]:
        return []  # no name search on Binance; registry delegates crypto search to CoinGecko
