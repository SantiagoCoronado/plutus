import hashlib
import json
import random
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import redis

from app.core.logging import get_logger
from app.providers.base import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    RateLimit,
)

log = get_logger(__name__)

# Atomic token bucket: returns -1 when a token was taken, else seconds to wait.
TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local last = tonumber(data[2])
if tokens == nil or last == nil then
  tokens = capacity
  last = now
end
tokens = math.min(capacity, tokens + math.max(0, now - last) * refill_rate)
local result
if tokens >= cost then
  tokens = tokens - cost
  result = '-1'
else
  result = tostring((cost - tokens) / refill_rate)
end
redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, 7200)
return result
"""

# How long a caller will wait for a token before giving up. This must stay above
# the slowest provider's steady-state seconds_per_token, or every call after the
# initial burst is refused outright (test_rate_limit_burst pins the relation).
DEFAULT_ACQUIRE_TIMEOUT_S = 120.0

MAX_ATTEMPTS = 5
BACKOFF_BASE_S = 1.0
BACKOFF_FACTOR = 2.0
BACKOFF_CAP_S = 60.0
BACKOFF_JITTER = 0.2


class BudgetExceeded(ProviderRateLimitError):
    """Hard daily/monthly budget hit — no HTTP call was made."""


class RateLimitedClient:
    """Provider HTTP client: Redis token bucket -> hard budgets -> TTL cache -> backoff.

    Sync by design: used from Celery tasks and FastAPI threadpool endpoints.
    Injectable clock/sleep keep the retry and bucket math unit-testable.
    """

    def __init__(
        self,
        provider: str,
        base_url: str,
        redis_client: redis.Redis,
        limits: RateLimit,
        *,
        timeout: float = 20.0,
        default_headers: dict[str, str] | None = None,
        clock=time.time,
        sleep=time.sleep,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.provider = provider
        self.limits = limits
        self._redis = redis_client
        self._clock = clock
        self._sleep = sleep
        self._bucket = redis_client.register_script(TOKEN_BUCKET_LUA)
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers=default_headers or {},
            transport=transport,
        )

    # -- public ---------------------------------------------------------------

    def get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        cache_ttl: int | None = None,
        acquire_timeout: float = DEFAULT_ACQUIRE_TIMEOUT_S,
        cache_key_exclude: tuple[str, ...] = ("token", "apikey", "api_key"),
        tolerate_statuses: tuple[int, ...] = (),
        headers: dict[str, str] | None = None,
    ) -> Any:
        """GET path, honoring cache, budgets, token bucket, and retry/backoff.

        `tolerate_statuses`: error codes whose JSON body the caller interprets itself
        (e.g. Twelve Data answers 400 with a semantic error payload). Never cached.
        `headers`: per-request headers (e.g. a signed Authorization). Signed callers
        embed their query string in `path` and pass no params, so the request line
        matches exactly what they signed.
        """
        params = params or {}
        cache_key = self._cache_key(path, params, cache_key_exclude)

        if cache_ttl:
            cached = self._redis.get(cache_key)
            if cached is not None:
                return json.loads(cached)

        # Refuse before spending a token (a rejected call must not wait out the
        # bucket), but charge only once the call is actually going to be made.
        self._assert_budget()
        self._acquire_token(acquire_timeout)
        self._charge_budget()
        payload, tolerated = self._request_with_backoff(
            path, params, tolerate_statuses, headers
        )

        if cache_ttl and not tolerated:
            self._redis.setex(cache_key, cache_ttl, json.dumps(payload))
        return payload

    # -- internals ------------------------------------------------------------

    def _cache_key(self, path: str, params: dict[str, Any], exclude: tuple[str, ...]) -> str:
        material = json.dumps(
            [path, sorted((k, str(v)) for k, v in params.items() if k.lower() not in exclude)]
        )
        return f"cache:{self.provider}:{hashlib.sha256(material.encode()).hexdigest()}"

    def _budget_windows(self) -> list[tuple[str, int, str, int]]:
        """(label, budget, redis key, key ttl) for each configured hard budget."""
        now = datetime.now(UTC)
        windows = []
        if self.limits.day_budget is not None:
            windows.append(
                ("daily", self.limits.day_budget, f"budget:{self.provider}:day:{now:%Y%m%d}",
                 48 * 3600)
            )
        if self.limits.month_budget is not None:
            windows.append(
                ("monthly", self.limits.month_budget, f"budget:{self.provider}:month:{now:%Y%m}",
                 40 * 24 * 3600)
            )
        return windows

    def _assert_budget(self) -> None:
        """Raise if a hard budget is already spent. Read-only on purpose.

        The counters double as the Settings page's usage gauge, so they must
        count calls *made*, not calls attempted: charging here would let a
        caller that retries on BudgetExceeded (the quote poller does, every 15s)
        drive the reported usage to several times the budget it never spent.

        Splitting the check from the charge lets concurrent callers race past
        the cap by at most one call each. That is deliberate: PROVIDER_LIMITS
        already sits ~10% under each published free tier, so a couple of extra
        calls are free, and the alternative (charge first) bills every request
        the token bucket later refuses."""
        for label, budget, key, _ttl in self._budget_windows():
            raw = self._redis.get(key)
            used = int(raw) if raw is not None else 0
            if used >= budget:
                raise BudgetExceeded(f"{self.provider}: {label} budget {budget} exhausted")

    def _charge_budget(self) -> None:
        """Book one unit against every hard budget — called once the request is certain."""
        for _label, _budget, key, ttl in self._budget_windows():
            self._redis.incr(key)
            self._redis.expire(key, ttl)

    def _check_budget(self) -> None:
        """Reserve one unit: assert then charge. Kept for callers that do both at once."""
        self._assert_budget()
        self._charge_budget()

    def _acquire_token(self, acquire_timeout: float) -> None:
        deadline = self._clock() + acquire_timeout
        while True:
            result = self._bucket(
                keys=[f"rl:{self.provider}"],
                args=[self.limits.capacity, self.limits.refill_rate, self._clock(), 1],
            )
            wait = float(result)
            if wait < 0:
                return
            if self._clock() + wait > deadline:
                raise ProviderRateLimitError(
                    f"{self.provider}: rate-limit wait {wait:.1f}s exceeds "
                    f"acquire timeout {acquire_timeout:.1f}s"
                )
            self._sleep(min(wait, 5.0))

    def _request_with_backoff(
        self,
        path: str,
        params: dict[str, Any],
        tolerate_statuses: tuple[int, ...] = (),
        headers: dict[str, str] | None = None,
    ) -> tuple[Any, bool]:
        last_error: str = ""
        for attempt in range(MAX_ATTEMPTS):
            try:
                resp = self._client.get(path, params=params or None, headers=headers)
            except httpx.HTTPError as exc:  # network/timeout: retryable
                last_error = f"transport error: {exc}"
                self._sleep(self._backoff_delay(attempt, None))
                continue

            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}"
                if attempt < MAX_ATTEMPTS - 1:
                    self._sleep(self._backoff_delay(attempt, resp.headers.get("Retry-After")))
                continue
            if resp.status_code in tolerate_statuses:
                return resp.json(), True
            if resp.status_code in (401, 403):
                raise ProviderAuthError(f"{self.provider}: HTTP {resp.status_code} — check API key")
            if resp.status_code >= 400:
                raise ProviderError(
                    f"{self.provider}: HTTP {resp.status_code} for {path}: {resp.text[:200]}"
                )
            return resp.json(), False

        if last_error.startswith("HTTP 429"):
            raise ProviderRateLimitError(f"{self.provider}: still 429 after {MAX_ATTEMPTS} tries")
        raise ProviderError(f"{self.provider}: giving up after {MAX_ATTEMPTS} tries ({last_error})")

    def _backoff_delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), BACKOFF_CAP_S)
            except ValueError:
                pass
        delay = min(BACKOFF_BASE_S * (BACKOFF_FACTOR**attempt), BACKOFF_CAP_S)
        return delay * (1 + random.uniform(-BACKOFF_JITTER, BACKOFF_JITTER))


def redis_from_url(url: str) -> redis.Redis:
    return redis.Redis.from_url(url, decode_responses=True)
