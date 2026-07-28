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
    """Token bucket + hard budgets, set at ~90% of published free-tier limits.

    `capacity` is the BURST allowance, not the rate. A bucket starts full, so the
    most it can issue in one refill period is capacity + refill_amount — set
    capacity == refill_amount (as every entry below once did) and the bucket
    quietly permits twice the intended rate for the first period.

    That is not theoretical: with capacity=45/refill=45 per hour against Tiingo's
    50/hour cap, the nightly EOD job issued 51 calls in its first ten minutes,
    was refused for the remaining fifty, and resumed the moment the clock hour
    rolled over. `published_per_period` records the real provider cap for the
    same window so __post_init__ can hold the line.
    """

    capacity: float
    refill_amount: float
    refill_period_s: float
    day_budget: int | None = None
    month_budget: int | None = None
    published_per_period: float | None = None

    def __post_init__(self) -> None:
        if self.published_per_period is None:
            return
        if self.worst_case_per_period > self.published_per_period:
            raise ValueError(
                f"rate limit permits {self.worst_case_per_period} per "
                f"{self.refill_period_s}s but the provider publishes "
                f"{self.published_per_period} — lower `capacity`, not the refill rate"
            )

    @property
    def refill_rate(self) -> float:
        return self.refill_amount / self.refill_period_s

    @property
    def worst_case_per_period(self) -> float:
        """Most requests issuable in one refill period, starting from a full bucket."""
        return self.capacity + self.refill_amount

    @property
    def seconds_per_token(self) -> float:
        """Steady-state pacing once the burst is spent — what a long batch job sees."""
        return 1.0 / self.refill_rate


# Published free tiers (mid-2026) are recorded as published_per_period so the
# capacity + refill_amount <= published invariant is enforced at import time:
#   tiingo:      50 req/hr, 1,000 req/day, 500 unique symbols/mo (symbol cap not code-enforced)
#   coingecko:   ~30 req/min demo key, 10,000 req/mo
#   twelvedata:  8 credits/min, 800 credits/day
#   finnhub:     60 req/min
#   alphavantage: ~5 req/min, 25 req/day
#   binance:     6000 weight/min, klines cost 2 -> 3000 req/min; we use 300 (~10%), keyless
#   fmp:         250 req/day (no published per-minute figure; 8/min is polite)
#   edgar:       SEC cap 10 req/s, keyless (mandatory User-Agent with contact email)
#   bitso:       private endpoints 60 req/min
# Each entry keeps a small burst and spends the rest of its allowance on the
# refill rate, so a long batch job paces evenly instead of front-loading a burst
# that trips the provider's window and then starves for the rest of it.
PROVIDER_LIMITS: dict[str, RateLimit] = {
    "tiingo": RateLimit(
        capacity=5, refill_amount=40, refill_period_s=3600,
        day_budget=900, published_per_period=50,
    ),
    "coingecko": RateLimit(
        capacity=5, refill_amount=22, refill_period_s=60,
        month_budget=9000, published_per_period=30,
    ),
    "twelvedata": RateLimit(
        capacity=2, refill_amount=5, refill_period_s=60,
        day_budget=750, published_per_period=8,
    ),
    "finnhub": RateLimit(
        capacity=6, refill_amount=48, refill_period_s=60, published_per_period=60
    ),
    "alphavantage": RateLimit(
        capacity=1, refill_amount=3, refill_period_s=60,
        day_budget=23, published_per_period=5,
    ),
    "binance": RateLimit(
        capacity=50, refill_amount=250, refill_period_s=60, published_per_period=3000
    ),
    "fmp": RateLimit(
        capacity=2, refill_amount=5, refill_period_s=60,
        day_budget=225, published_per_period=8,
    ),
    "edgar": RateLimit(
        capacity=2, refill_amount=7, refill_period_s=1, published_per_period=10
    ),
    "bitso": RateLimit(
        capacity=6, refill_amount=48, refill_period_s=60, published_per_period=60
    ),
}

# Response-cache TTLs per data type (spec §3)
TTL_DAILY_BARS = 12 * 3600
TTL_QUOTE = 60
TTL_SEARCH = 24 * 3600
TTL_FUNDAMENTALS = 24 * 3600
TTL_NEWS = 600  # one 15-min poll cycle


def empty_candles() -> pd.DataFrame:
    return pd.DataFrame(columns=CANDLE_COLUMNS)
