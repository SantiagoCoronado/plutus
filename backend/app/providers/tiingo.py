from datetime import date

import pandas as pd

from app.providers.base import (
    CANDLE_COLUMNS,
    TTL_DAILY_BARS,
    TTL_QUOTE,
    TTL_SEARCH,
    ProviderError,
    ProviderNotConfigured,
    empty_candles,
)
from app.providers.http import RateLimitedClient
from app.schemas.common import AssetClass, Interval, Quote, SymbolInfo

BASE_URL = "https://api.tiingo.com"


class TiingoProvider:
    """US stocks/ETFs EOD. Stores the ADJUSTED series (adjOpen/../adjVolume) so
    Phase 2 indicators are split/dividend-consistent — deliberate decision, see plan."""

    name = "tiingo"

    def __init__(self, client: RateLimitedClient, api_key: str) -> None:
        self._client = client
        self._api_key = api_key

    def _require_key(self) -> None:
        if not self._api_key:
            raise ProviderNotConfigured("tiingo: TIINGO_API_KEY is not set")

    def get_ohlcv(
        self, symbol: str, asset_class: AssetClass, interval: Interval, start: date, end: date
    ) -> pd.DataFrame:
        self._require_key()
        if interval != Interval.d1:
            raise ProviderError(f"tiingo adapter supports only 1d in Phase 1, got {interval}")
        payload = self._client.get_json(
            f"/tiingo/daily/{symbol}/prices",
            {
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "format": "json",
                "token": self._api_key,
            },
            cache_ttl=TTL_DAILY_BARS,
        )
        return self._parse_ohlcv(payload)

    @staticmethod
    def _parse_ohlcv(payload: list[dict]) -> pd.DataFrame:
        if not payload:
            return empty_candles()
        df = pd.DataFrame(payload)
        out = pd.DataFrame(
            {
                "ts": pd.to_datetime(df["date"], utc=True).dt.normalize(),
                "open": df["adjOpen"].astype(float),
                "high": df["adjHigh"].astype(float),
                "low": df["adjLow"].astype(float),
                "close": df["adjClose"].astype(float),
                "volume": df["adjVolume"].astype(float),
            }
        )
        return out.sort_values("ts").reset_index(drop=True)[CANDLE_COLUMNS]

    def get_quote(self, symbol: str, asset_class: AssetClass) -> Quote:
        self._require_key()
        payload = self._client.get_json(
            f"/iex/{symbol}", {"token": self._api_key}, cache_ttl=TTL_QUOTE, acquire_timeout=3.0
        )
        if not payload:
            raise ProviderError(f"tiingo: no quote for {symbol}")
        row = payload[0]
        price = row.get("tngoLast") or row.get("last") or row.get("prevClose")
        if price is None:
            raise ProviderError(f"tiingo: quote for {symbol} has no price")
        return Quote(symbol=symbol, price=float(price), currency="USD", ts=row.get("timestamp"))

    def search_symbols(self, query: str) -> list[SymbolInfo]:
        self._require_key()
        payload = self._client.get_json(
            "/tiingo/utilities/search",
            {"query": query, "token": self._api_key},
            cache_ttl=TTL_SEARCH,
            acquire_timeout=3.0,
        )
        results = []
        for item in payload or []:
            asset_type = (item.get("assetType") or "").lower()
            results.append(
                SymbolInfo(
                    symbol=item.get("ticker", ""),
                    name=item.get("name", ""),
                    asset_class=AssetClass.etf if asset_type == "etf" else AssetClass.stock,
                    exchange=item.get("exchange") or None,
                    currency=(item.get("priceCurrency") or "USD").upper(),
                    provider=self.name,
                    provider_symbol=item.get("ticker", ""),
                )
            )
        return results
