import httpx
import pytest
import respx

from app.providers.base import ProviderAuthError, ProviderError, ProviderRateLimitError, RateLimit
from app.providers.http import RateLimitedClient

BASE = "https://api.test"
WIDE_OPEN = RateLimit(capacity=1000, refill_amount=1000, refill_period_s=1)


@pytest.fixture
def client(fake_redis, fake_clock):
    return RateLimitedClient(
        "testprov", BASE, fake_redis, WIDE_OPEN, clock=fake_clock, sleep=fake_clock.sleep
    )


@respx.mock(base_url=BASE)
def test_retries_5xx_then_succeeds(respx_mock, client):
    route = respx_mock.get("/data").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(502),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    assert client.get_json("/data") == {"ok": True}
    assert route.call_count == 3


@respx.mock(base_url=BASE)
def test_429_honors_retry_after(respx_mock, client, fake_clock):
    route = respx_mock.get("/data").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json={"ok": 1}),
        ]
    )
    assert client.get_json("/data") == {"ok": 1}
    assert route.call_count == 2
    assert 7.0 in fake_clock.sleeps


@respx.mock(base_url=BASE)
def test_404_raises_without_retry(respx_mock, client):
    route = respx_mock.get("/nope").mock(return_value=httpx.Response(404))
    with pytest.raises(ProviderError):
        client.get_json("/nope")
    assert route.call_count == 1


@respx.mock(base_url=BASE)
def test_401_maps_to_auth_error(respx_mock, client):
    respx_mock.get("/data").mock(return_value=httpx.Response(401))
    with pytest.raises(ProviderAuthError):
        client.get_json("/data")


@respx.mock(base_url=BASE)
def test_gives_up_after_max_attempts(respx_mock, client):
    route = respx_mock.get("/data").mock(return_value=httpx.Response(500))
    with pytest.raises(ProviderError, match="giving up"):
        client.get_json("/data")
    assert route.call_count == 5


@respx.mock(base_url=BASE)
def test_persistent_429_raises_rate_limit_error(respx_mock, client):
    respx_mock.get("/data").mock(return_value=httpx.Response(429))
    with pytest.raises(ProviderRateLimitError):
        client.get_json("/data")


@respx.mock(base_url=BASE)
def test_cache_hit_skips_http_and_budget(respx_mock, fake_redis, fake_clock):
    limits = RateLimit(capacity=1000, refill_amount=1000, refill_period_s=1, day_budget=100)
    client = RateLimitedClient(
        "testprov", BASE, fake_redis, limits, clock=fake_clock, sleep=fake_clock.sleep
    )
    route = respx_mock.get("/bars").mock(return_value=httpx.Response(200, json={"bars": [1, 2]}))

    first = client.get_json("/bars", {"symbol": "AAPL"}, cache_ttl=3600)
    second = client.get_json("/bars", {"symbol": "AAPL"}, cache_ttl=3600)

    assert first == second == {"bars": [1, 2]}
    assert route.call_count == 1
    day_keys = fake_redis.keys("budget:testprov:day:*")
    assert len(day_keys) == 1
    assert int(fake_redis.get(day_keys[0])) == 1  # budget charged once, not twice


@respx.mock(base_url=BASE)
def test_cache_key_ignores_api_key_params(respx_mock, client):
    route = respx_mock.get("/bars").mock(return_value=httpx.Response(200, json={"bars": []}))

    client.get_json("/bars", {"symbol": "AAPL", "token": "aaa"}, cache_ttl=3600)
    client.get_json("/bars", {"symbol": "AAPL", "token": "bbb"}, cache_ttl=3600)
    assert route.call_count == 1  # same logical request -> cached

    client.get_json("/bars", {"symbol": "MSFT", "token": "aaa"}, cache_ttl=3600)
    assert route.call_count == 2  # different symbol -> real request


@respx.mock(base_url=BASE)
def test_cache_respects_ttl_expiry(respx_mock, fake_redis, fake_clock, client):
    route = respx_mock.get("/q").mock(return_value=httpx.Response(200, json={"p": 1}))

    client.get_json("/q", cache_ttl=30)
    # fakeredis TTL is wall-clock based; emulate expiry by deleting the key
    for key in fake_redis.keys("cache:testprov:*"):
        fake_redis.delete(key)
    client.get_json("/q", cache_ttl=30)
    assert route.call_count == 2
