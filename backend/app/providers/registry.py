import redis

from app.core.config import get_settings
from app.providers.base import PROVIDER_LIMITS, MarketDataProvider, ProviderNotConfigured
from app.providers.coingecko import BASE_URL as COINGECKO_URL
from app.providers.coingecko import CoinGeckoProvider
from app.providers.http import RateLimitedClient, redis_from_url
from app.providers.tiingo import BASE_URL as TIINGO_URL
from app.providers.tiingo import TiingoProvider
from app.providers.twelvedata import BASE_URL as TWELVEDATA_URL
from app.providers.twelvedata import TwelveDataProvider
from app.schemas.common import AssetClass

_instances: dict[str, MarketDataProvider] = {}
_redis: redis.Redis | None = None


def _shared_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis_from_url(get_settings().redis_url)
    return _redis


def _build(name: str) -> MarketDataProvider:
    if name in _instances:
        return _instances[name]
    settings = get_settings()
    if name == "tiingo":
        client = RateLimitedClient("tiingo", TIINGO_URL, _shared_redis(), PROVIDER_LIMITS["tiingo"])
        provider: MarketDataProvider = TiingoProvider(client, settings.tiingo_api_key)
    elif name == "coingecko":
        headers = (
            {"x-cg-demo-api-key": settings.coingecko_api_key} if settings.coingecko_api_key else {}
        )
        client = RateLimitedClient(
            "coingecko",
            COINGECKO_URL,
            _shared_redis(),
            PROVIDER_LIMITS["coingecko"],
            default_headers=headers,
        )
        provider = CoinGeckoProvider(client, settings.coingecko_api_key)
    elif name == "twelvedata":
        client = RateLimitedClient(
            "twelvedata", TWELVEDATA_URL, _shared_redis(), PROVIDER_LIMITS["twelvedata"]
        )
        provider = TwelveDataProvider(client, settings.twelvedata_api_key)
    elif name in ("finnhub", "alphavantage"):
        # config switch exists per spec ("configuration, not code"); adapters are post-Phase 1
        raise ProviderNotConfigured(f"{name}: adapter not yet implemented")
    else:
        raise ProviderNotConfigured(f"unknown provider '{name}'")
    _instances[name] = provider
    return provider


def get_provider(asset_class: AssetClass | str) -> MarketDataProvider:
    settings = get_settings()
    asset_class = AssetClass(asset_class)
    match asset_class:
        case AssetClass.stock | AssetClass.etf:
            return _build(settings.provider_stocks)
        case AssetClass.crypto:
            return _build(settings.provider_crypto)
        case AssetClass.forex:
            return _build(settings.provider_forex)


def configured_providers() -> list[MarketDataProvider]:
    """All distinct providers currently wired via config (for search merging)."""
    providers: list[MarketDataProvider] = []
    seen: set[str] = set()
    for asset_class in (AssetClass.stock, AssetClass.crypto, AssetClass.forex):
        try:
            provider = get_provider(asset_class)
        except ProviderNotConfigured:
            continue
        if provider.name not in seen:
            seen.add(provider.name)
            providers.append(provider)
    return providers


def reset_registry() -> None:
    """Test hook: drop cached provider/redis instances."""
    global _redis
    _instances.clear()
    _redis = None
