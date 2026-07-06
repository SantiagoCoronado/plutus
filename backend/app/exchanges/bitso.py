"""Bitso API v3 connector — READ-ONLY BY CONSTRUCTION.

There is exactly one private HTTP verb here: `_get`. No `_post`/`_put`/`_delete`
method exists, so no code path can place, modify, or cancel an order. The public
methods wrap only balance/history endpoints; no order/trading path is referenced.

Request signing (Bitso HMAC-SHA256):
    nonce   = strictly increasing milliseconds timestamp
    message = nonce + HTTP_METHOD + request_path + json_body
              (request_path includes "/v3/..." AND any query string; body empty for GET)
    signature = hex HMAC-SHA256(secret, message)
    header    = "Authorization: Bitso <key>:<nonce>:<signature>"

Because the signature covers the exact path+query, this client composes the full
path-with-query itself and passes it to get_json with no params — get_json then
sends that path verbatim (empty params → no re-encoding), so the signed string
and the wire request are identical. Signed calls are never cached.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.exchanges.base import (
    ExchangeBalance,
    ExchangeError,
    ExchangeFunding,
    ExchangeTrade,
    ExchangeWithdrawal,
)
from app.providers.base import PROVIDER_LIMITS
from app.providers.http import RateLimitedClient

BASE_URL = "https://api.bitso.com"
PAGE_LIMIT = 100


class BitsoClient:
    name = "bitso"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        client: RateLimitedClient,
        *,
        nonce_factory: Callable[[], int] | None = None,
    ) -> None:
        self._key = api_key
        self._secret = api_secret
        self._client = client
        self._nonce_factory = nonce_factory or (lambda: int(time.time() * 1000))
        self._last_nonce = 0

    # -- signing --------------------------------------------------------------

    def _next_nonce(self) -> int:
        # strictly increasing per key, even for calls landing in the same millisecond
        nonce = max(self._nonce_factory(), self._last_nonce + 1)
        self._last_nonce = nonce
        return nonce

    def _auth_header(self, method: str, request_path: str, body: str = "") -> str:
        nonce = self._next_nonce()
        message = f"{nonce}{method}{request_path}{body}"
        signature = hmac.new(
            self._secret.encode(), message.encode(), hashlib.sha256
        ).hexdigest()
        return f"Bitso {self._key}:{nonce}:{signature}"

    @staticmethod
    def _compose(path: str, params: dict[str, Any] | None) -> str:
        if not params:
            return path
        # values are ids / small ints / 'asc' — URL-safe, so this is what the wire sees
        query = "&".join(f"{key}={value}" for key, value in params.items())
        return f"{path}?{query}"

    # -- the only private verb: GET -------------------------------------------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        request_path = self._compose(path, params)
        header = self._auth_header("GET", request_path)
        payload = self._client.get_json(
            request_path, headers={"Authorization": header}
        )
        if not isinstance(payload, dict) or not payload.get("success"):
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            message = error.get("message") if isinstance(error, dict) else None
            raise ExchangeError(f"bitso: {message or 'request did not succeed'}")
        return payload["payload"]

    # -- public read surface --------------------------------------------------

    def fetch_balances(self) -> list[ExchangeBalance]:
        payload = self._get("/v3/balance")
        return [
            ExchangeBalance(
                currency=str(row["currency"]).upper(),
                total=float(row.get("total", 0) or 0),
                available=float(row.get("available", 0) or 0),
                locked=float(row.get("locked", 0) or 0),
            )
            for row in payload.get("balances", [])
        ]

    def fetch_trades(self, since_tid: str | None = None) -> list[ExchangeTrade]:
        params: dict[str, Any] = {"sort": "asc", "limit": PAGE_LIMIT}
        if since_tid:
            params = {"marker": since_tid, "sort": "asc", "limit": PAGE_LIMIT}
        rows = self._get("/v3/user_trades", params)
        return [
            ExchangeTrade(
                tid=str(row["tid"]),
                book=str(row["book"]),
                side=str(row["side"]),
                major=float(row["major"]),
                minor=float(row["minor"]),
                price=float(row["price"]),
                fees_amount=float(row.get("fees_amount", 0) or 0),
                fees_currency=str(row.get("fees_currency", "") or ""),
                created_at=_parse_ts(row["created_at"]),
            )
            for row in rows
        ]

    def fetch_fundings(self, since_id: str | None = None) -> list[ExchangeFunding]:
        params: dict[str, Any] = {"sort": "asc", "limit": PAGE_LIMIT}
        if since_id:
            params = {"marker": since_id, "sort": "asc", "limit": PAGE_LIMIT}
        rows = self._get("/v3/fundings", params)
        return [
            ExchangeFunding(
                fid=str(row["fid"]),
                currency=str(row["currency"]).upper(),
                amount=float(row["amount"]),
                status=str(row.get("status", "")),
                created_at=_parse_ts(row["created_at"]),
                method=row.get("method"),
            )
            for row in rows
        ]

    def fetch_withdrawals(self, since_id: str | None = None) -> list[ExchangeWithdrawal]:
        params: dict[str, Any] = {"sort": "asc", "limit": PAGE_LIMIT}
        if since_id:
            params = {"marker": since_id, "sort": "asc", "limit": PAGE_LIMIT}
        rows = self._get("/v3/withdrawals", params)
        return [
            ExchangeWithdrawal(
                wid=str(row["wid"]),
                currency=str(row["currency"]).upper(),
                amount=float(row["amount"]),
                status=str(row.get("status", "")),
                created_at=_parse_ts(row["created_at"]),
                method=row.get("method"),
            )
            for row in rows
        ]


def _parse_ts(value: str) -> datetime:
    # Bitso timestamps are ISO 8601 with an explicit offset, e.g. 2026-04-08T17:52:31+00:00
    return datetime.fromisoformat(value)


def build_bitso_client(api_key: str, api_secret: str, redis_client=None) -> BitsoClient:
    """Wire a BitsoClient over a rate-limited HTTP client (shared Redis token bucket)."""
    if redis_client is None:
        from app.providers.registry import _shared_redis

        redis_client = _shared_redis()
    http = RateLimitedClient("bitso", BASE_URL, redis_client, PROVIDER_LIMITS["bitso"])
    return BitsoClient(api_key, api_secret, http)
