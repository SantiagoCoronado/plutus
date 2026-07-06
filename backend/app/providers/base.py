from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

import pandas as pd

from app.schemas.common import AssetClass, Interval, Quote, SymbolInfo

# Canonical get_ohlcv() DataFrame columns; ts is tz-aware UTC (midnight of bar date for '1d')
CANDLE_COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


class ProviderError(Exception):
    """Base for all provider failures."""


class ProviderAuthError(ProviderError):
    """Missing/invalid API key (HTTP 401/403)."""


class ProviderRateLimitError(ProviderError):
    """Rate limit or hard budget exceeded; retrying now would burn free tier."""


class ProviderNotConfigured(ProviderError):
    """Provider selected in config but not usable (no adapter or no API key)."""


@runtime_checkable
class MarketDataProvider(Protocol):
    name: str

    def get_ohlcv(
        self,
        symbol: str,
        asset_class: AssetClass,
        interval: Interval,
        start: date,
        end: date,
    ) -> pd.DataFrame: ...

    def get_quote(self, symbol: str, asset_class: AssetClass) -> Quote: ...

    def search_symbols(self, query: str) -> list[SymbolInfo]: ...


@dataclass(frozen=True)
class RateLimit:
    """Token bucket + hard budgets, set at ~90% of published free-tier limits."""

    capacity: float
    refill_amount: float
    refill_period_s: float
    day_budget: int | None = None
    month_budget: int | None = None

    @property
    def refill_rate(self) -> float:
        return self.refill_amount / self.refill_period_s


# Published free tiers (mid-2026):
#   tiingo:      50 req/hr, 1,000 req/day, 500 unique symbols/mo (symbol cap not code-enforced)
#   coingecko:   ~30 req/min demo key, 10,000 req/mo
#   twelvedata:  8 credits/min, 800 credits/day
#   finnhub:     60 req/min
#   alphavantage: ~5 req/min, 25 req/day
#   binance:     6000 weight/min, klines cost 2 — 300 req/min is ~10% utilization, keyless
#   fmp:         250 req/day (no published per-minute figure; 8/min is polite)
#   edgar:       SEC cap 10 req/s, keyless (mandatory User-Agent with contact email)
#   bitso:       private endpoints 60 req/min — 55/min stays under with headroom
PROVIDER_LIMITS: dict[str, RateLimit] = {
    "tiingo": RateLimit(capacity=45, refill_amount=45, refill_period_s=3600, day_budget=900),
    "coingecko": RateLimit(capacity=25, refill_amount=25, refill_period_s=60, month_budget=9000),
    "twelvedata": RateLimit(capacity=7, refill_amount=7, refill_period_s=60, day_budget=750),
    "finnhub": RateLimit(capacity=55, refill_amount=55, refill_period_s=60),
    "alphavantage": RateLimit(capacity=4, refill_amount=4, refill_period_s=60, day_budget=23),
    "binance": RateLimit(capacity=300, refill_amount=300, refill_period_s=60),
    "fmp": RateLimit(capacity=8, refill_amount=8, refill_period_s=60, day_budget=225),
    "edgar": RateLimit(capacity=8, refill_amount=8, refill_period_s=1),
    "bitso": RateLimit(capacity=55, refill_amount=55, refill_period_s=60),
}

# Response-cache TTLs per data type (spec §3)
TTL_DAILY_BARS = 12 * 3600
TTL_QUOTE = 60
TTL_SEARCH = 24 * 3600
TTL_FUNDAMENTALS = 24 * 3600
TTL_NEWS = 600  # one 15-min poll cycle


def empty_candles() -> pd.DataFrame:
    return pd.DataFrame(columns=CANDLE_COLUMNS)
