import redis

from app.core.config import get_settings
from app.providers.base import PROVIDER_LIMITS, MarketDataProvider, ProviderNotConfigured
from app.providers.binance import BASE_URL as BINANCE_URL
from app.providers.binance import BinanceProvider
from app.providers.coingecko import BASE_URL as COINGECKO_URL
from app.providers.coingecko import CoinGeckoProvider
from app.providers.http import RateLimitedClient, redis_from_url
from app.providers.tiingo import BASE_URL as TIINGO_URL
from app.providers.tiingo import TiingoProvider
from app.providers.twelvedata import BASE_URL as TWELVEDATA_URL
from app.providers.twelvedata import TwelveDataProvider
from app.schemas.common import AssetClass

# Providers without name search delegate the search surface to a richer catalog
# (Binance OHLCV + CoinGecko search/metadata is the intended crypto pairing)
SEARCH_DELEGATES: dict[str, str] = {"binance": "coingecko"}

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
    elif name == "binance":
        client = RateLimitedClient(
            "binance", BINANCE_URL, _shared_redis(), PROVIDER_LIMITS["binance"]
        )
        provider = BinanceProvider(client)
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


def search_providers() -> list[MarketDataProvider]:
    """configured_providers() with search-less providers swapped for their delegates."""
    providers: list[MarketDataProvider] = []
    seen: set[str] = set()
    for provider in configured_providers():
        name = SEARCH_DELEGATES.get(provider.name, provider.name)
        if name in seen:
            continue
        seen.add(name)
        try:
            providers.append(_build(name))
        except ProviderNotConfigured:
            continue
    return providers


def get_fundamentals_provider():
    """FundamentalsProvider per PROVIDER_FUNDAMENTALS (cached under a prefixed key —
    'finnhub'/'fmp' names must not collide with market-data provider instances)."""
    settings = get_settings()
    name = settings.provider_fundamentals
    cache_key = f"fundamentals:{name}"
    if cache_key in _instances:
        return _instances[cache_key]
    if name == "fmp":
        from app.providers.fmp import BASE_URL as FMP_URL
        from app.providers.fmp import FMPProvider

        client = RateLimitedClient("fmp", FMP_URL, _shared_redis(), PROVIDER_LIMITS["fmp"])
        provider = FMPProvider(client, settings.fmp_api_key)
    elif name == "edgar":
        from app.providers.edgar import BASE_URL as EDGAR_URL
        from app.providers.edgar import USER_AGENT, EdgarProvider

        headers = {"User-Agent": USER_AGENT}
        client = RateLimitedClient(
            "edgar", EDGAR_URL, _shared_redis(), PROVIDER_LIMITS["edgar"], default_headers=headers
        )
        ticker_client = RateLimitedClient(
            "edgar",
            "https://www.sec.gov",
            _shared_redis(),
            PROVIDER_LIMITS["edgar"],
            default_headers=headers,
        )
        provider = EdgarProvider(client, ticker_client)
    else:
        raise ProviderNotConfigured(f"unknown fundamentals provider '{name}'")
    _instances[cache_key] = provider
    return provider


def get_news_provider():
    settings = get_settings()
    name = settings.provider_news
    cache_key = f"news:{name}"
    if cache_key in _instances:
        return _instances[cache_key]
    if name == "finnhub":
        from app.providers.finnhub import BASE_URL as FINNHUB_URL
        from app.providers.finnhub import FinnhubNewsProvider

        client = RateLimitedClient(
            "finnhub", FINNHUB_URL, _shared_redis(), PROVIDER_LIMITS["finnhub"]
        )
        provider = FinnhubNewsProvider(client, settings.finnhub_api_key)
    else:
        raise ProviderNotConfigured(f"unknown news provider '{name}'")
    _instances[cache_key] = provider
    return provider


def reset_registry() -> None:
    """Test hook: drop cached provider/redis instances."""
    global _redis
    _instances.clear()
    _redis = None
