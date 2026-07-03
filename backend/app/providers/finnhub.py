from datetime import UTC, date, datetime

from app.providers.base import TTL_NEWS, ProviderNotConfigured
from app.providers.http import RateLimitedClient
from app.schemas.news import NewsItemIn

BASE_URL = "https://finnhub.io/api/v1"


class FinnhubNewsProvider:
    """Company news, free tier (US/North-American listed companies).
    Sentiment is a premium endpoint — news_items.sentiment stays NULL for now."""

    name = "finnhub"

    def __init__(self, client: RateLimitedClient, api_key: str) -> None:
        self._client = client
        self._api_key = api_key

    def _require_key(self) -> None:
        if not self._api_key:
            raise ProviderNotConfigured("finnhub: FINNHUB_API_KEY is not set")

    def get_company_news(self, symbol: str, start: date, end: date) -> list[NewsItemIn]:
        self._require_key()
        payload = self._client.get_json(
            "/company-news",
            {
                "symbol": symbol,
                "from": start.isoformat(),
                "to": end.isoformat(),
                "token": self._api_key,
            },
            cache_ttl=TTL_NEWS,
        )
        items: list[NewsItemIn] = []
        for row in payload or []:
            url = row.get("url")
            headline = row.get("headline")
            ts = row.get("datetime")
            if not url or not headline or not ts:
                continue
            items.append(
                NewsItemIn(
                    ts=datetime.fromtimestamp(int(ts), tz=UTC),
                    source=row.get("source") or "finnhub",
                    headline=headline,
                    url=url,
                )
            )
        return items
