from datetime import UTC, date, datetime, timedelta

import pandas as pd

from app.providers.base import (
    CANDLE_COLUMNS,
    TTL_DAILY_BARS,
    TTL_QUOTE,
    TTL_SEARCH,
    ProviderError,
    empty_candles,
)
from app.providers.http import RateLimitedClient
from app.schemas.common import AssetClass, Interval, Quote, SymbolInfo

BASE_URL = "https://api.coingecko.com/api/v3"

# Demo/free tier: historical data capped at ~365 days
MAX_HISTORY_DAYS = 364


class CoinGeckoProvider:
    """Crypto daily candles, SYNTHESIZED from the close+volume series:
    the free tier's /ohlc endpoint degrades to 4-day candles beyond 30 days, so we use
    /market_chart/range and set open = previous close, high/low = max/min(open, close).
    H/L are approximations — flagged in the plan; real-OHLC exchange adapter is a
    Phase 2 follow-up. `symbol` here is the CoinGecko coin id (e.g. 'bitcoin')."""

    name = "coingecko"

    def __init__(self, client: RateLimitedClient, api_key: str = "") -> None:
        self._client = client
        self._api_key = api_key  # optional demo key, sent as header by the registry

    def get_ohlcv(
        self, symbol: str, asset_class: AssetClass, interval: Interval, start: date, end: date
    ) -> pd.DataFrame:
        if interval != Interval.d1:
            raise ProviderError(f"coingecko adapter supports only 1d in Phase 1, got {interval}")
        floor = date.today() - timedelta(days=MAX_HISTORY_DAYS)
        start = max(start, floor)
        if start > end:
            return empty_candles()
        frm = int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp())
        to = int(datetime(end.year, end.month, end.day, tzinfo=UTC).timestamp()) + 86400
        payload = self._client.get_json(
            f"/coins/{symbol}/market_chart/range",
            {"vs_currency": "usd", "from": frm, "to": to},
            cache_ttl=TTL_DAILY_BARS,
        )
        return self._parse_ohlcv(payload)

    @staticmethod
    def _parse_ohlcv(payload: dict) -> pd.DataFrame:
        prices = payload.get("prices") or []
        if not prices:
            return empty_candles()
        px = pd.DataFrame(prices, columns=["ms", "price"])
        px["ts"] = pd.to_datetime(px["ms"], unit="ms", utc=True).dt.normalize()
        # ranges under 90 days come back hourly — keep the last point per UTC day
        daily = px.groupby("ts", as_index=False).last()[["ts", "price"]]

        volumes = payload.get("total_volumes") or []
        if volumes:
            vol = pd.DataFrame(volumes, columns=["ms", "volume"])
            vol["ts"] = pd.to_datetime(vol["ms"], unit="ms", utc=True).dt.normalize()
            vol = vol.groupby("ts", as_index=False).last()[["ts", "volume"]]
            daily = daily.merge(vol, on="ts", how="left")
        else:
            daily["volume"] = None

        daily = daily.sort_values("ts").reset_index(drop=True)
        close = daily["price"].astype(float)
        open_ = close.shift(1).fillna(close)
        return pd.DataFrame(
            {
                "ts": daily["ts"],
                "open": open_,
                "high": pd.concat([open_, close], axis=1).max(axis=1),
                "low": pd.concat([open_, close], axis=1).min(axis=1),
                "close": close,
                "volume": daily["volume"].astype(float),
            }
        )[CANDLE_COLUMNS]

    def get_quote(self, symbol: str, asset_class: AssetClass) -> Quote:
        payload = self._client.get_json(
            "/simple/price",
            {"ids": symbol, "vs_currencies": "usd"},
            cache_ttl=TTL_QUOTE,
            acquire_timeout=3.0,
        )
        entry = payload.get(symbol)
        if not entry or "usd" not in entry:
            raise ProviderError(f"coingecko: no quote for {symbol}")
        return Quote(symbol=symbol, price=float(entry["usd"]), currency="USD")

    def search_symbols(self, query: str) -> list[SymbolInfo]:
        payload = self._client.get_json(
            "/search", {"query": query}, cache_ttl=TTL_SEARCH, acquire_timeout=3.0
        )
        results = []
        for coin in (payload.get("coins") or [])[:15]:
            results.append(
                SymbolInfo(
                    symbol=(coin.get("symbol") or "").upper(),
                    name=coin.get("name", ""),
                    asset_class=AssetClass.crypto,
                    exchange=None,
                    currency="USD",
                    provider=self.name,
                    provider_symbol=coin.get("id", ""),
                )
            )
        return results
