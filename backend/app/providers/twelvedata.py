from datetime import date

import pandas as pd

from app.providers.base import (
    CANDLE_COLUMNS,
    TTL_DAILY_BARS,
    TTL_QUOTE,
    TTL_SEARCH,
    ProviderAuthError,
    ProviderError,
    ProviderNotConfigured,
    ProviderRateLimitError,
    empty_candles,
)
from app.providers.http import RateLimitedClient
from app.schemas.common import AssetClass, Interval, Quote, SymbolInfo

BASE_URL = "https://api.twelvedata.com"


def _raise_on_error_payload(payload: dict) -> None:
    """Twelve Data reports errors as JSON with HTTP 200 — map them to our hierarchy."""
    if isinstance(payload, dict) and payload.get("status") == "error":
        code = payload.get("code")
        message = payload.get("message", "unknown error")
        if code == 429:
            raise ProviderRateLimitError(f"twelvedata: {message}")
        if code in (401, 403):
            raise ProviderAuthError(f"twelvedata: {message}")
        raise ProviderError(f"twelvedata: [{code}] {message}")


class TwelveDataProvider:
    """Forex daily bars — real OHLC, no volume for FX pairs.
    `symbol` here is the provider symbol (e.g. 'EUR/USD')."""

    name = "twelvedata"

    def __init__(self, client: RateLimitedClient, api_key: str) -> None:
        self._client = client
        self._api_key = api_key

    def _require_key(self) -> None:
        if not self._api_key:
            raise ProviderNotConfigured("twelvedata: TWELVEDATA_API_KEY is not set")

    def get_ohlcv(
        self, symbol: str, asset_class: AssetClass, interval: Interval, start: date, end: date
    ) -> pd.DataFrame:
        self._require_key()
        if interval != Interval.d1:
            raise ProviderError(f"twelvedata adapter supports only 1d in Phase 1, got {interval}")
        payload = self._client.get_json(
            "/time_series",
            {
                "symbol": symbol,
                "interval": "1day",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "outputsize": 5000,
                "order": "ASC",
                "apikey": self._api_key,
            },
            cache_ttl=TTL_DAILY_BARS,
        )
        return self._parse_ohlcv(payload)

    @staticmethod
    def _parse_ohlcv(payload: dict) -> pd.DataFrame:
        _raise_on_error_payload(payload)
        values = payload.get("values") or []
        if not values:
            return empty_candles()
        df = pd.DataFrame(values)
        out = pd.DataFrame(
            {
                "ts": pd.to_datetime(df["datetime"], utc=True).dt.normalize(),
                "open": df["open"].astype(float),
                "high": df["high"].astype(float),
                "low": df["low"].astype(float),
                "close": df["close"].astype(float),
                "volume": df["volume"].astype(float) if "volume" in df else None,
            }
        )
        return out.sort_values("ts").reset_index(drop=True)[CANDLE_COLUMNS]

    def get_quote(self, symbol: str, asset_class: AssetClass) -> Quote:
        self._require_key()
        payload = self._client.get_json(
            "/price",
            {"symbol": symbol, "apikey": self._api_key},
            cache_ttl=TTL_QUOTE,
            acquire_timeout=3.0,
        )
        _raise_on_error_payload(payload)
        if "price" not in payload:
            raise ProviderError(f"twelvedata: no quote for {symbol}")
        quote_currency = symbol.split("/")[-1] if "/" in symbol else "USD"
        return Quote(symbol=symbol, price=float(payload["price"]), currency=quote_currency)

    def search_symbols(self, query: str) -> list[SymbolInfo]:
        self._require_key()
        payload = self._client.get_json(
            "/symbol_search",
            {"symbol": query, "apikey": self._api_key},
            cache_ttl=TTL_SEARCH,
            acquire_timeout=3.0,
        )
        _raise_on_error_payload(payload)
        results = []
        for item in (payload.get("data") or [])[:15]:
            instrument_type = (item.get("instrument_type") or "").lower()
            if "currency" in instrument_type:
                asset_class = AssetClass.forex
            elif instrument_type == "etf":
                asset_class = AssetClass.etf
            else:
                asset_class = AssetClass.stock
            provider_symbol = item.get("symbol", "")
            results.append(
                SymbolInfo(
                    symbol=provider_symbol.replace("/", ""),
                    name=item.get("instrument_name", ""),
                    asset_class=asset_class,
                    exchange=item.get("exchange") or None,
                    currency=(item.get("currency_quote") or item.get("currency") or "USD"),
                    provider=self.name,
                    provider_symbol=provider_symbol,
                )
            )
        return results
