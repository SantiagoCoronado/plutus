from datetime import UTC, datetime

import pytest

from app.providers.base import ProviderRateLimitError, RateLimit
from app.providers.http import BudgetExceeded, RateLimitedClient


def make_client(fake_redis, fake_clock, limits: RateLimit) -> RateLimitedClient:
    return RateLimitedClient(
        "testprov",
        "https://api.test",
        fake_redis,
        limits,
        clock=fake_clock,
        sleep=fake_clock.sleep,
    )


def test_bucket_allows_up_to_capacity_then_waits(fake_redis, fake_clock):
    limits = RateLimit(capacity=2, refill_amount=1, refill_period_s=1)  # 1 token/s
    client = make_client(fake_redis, fake_clock, limits)

    client._acquire_token(acquire_timeout=60)
    client._acquire_token(acquire_timeout=60)
    assert fake_clock.sleeps == []  # two tokens available instantly

    client._acquire_token(acquire_timeout=60)  # empty -> must wait for refill
    assert len(fake_clock.sleeps) >= 1
    assert sum(fake_clock.sleeps) == pytest.approx(1.0, abs=0.05)


def test_bucket_refills_over_time(fake_redis, fake_clock):
    limits = RateLimit(capacity=5, refill_amount=5, refill_period_s=10)  # 0.5 token/s
    client = make_client(fake_redis, fake_clock, limits)

    for _ in range(5):
        client._acquire_token(acquire_timeout=60)
    fake_clock.now += 4  # 2 tokens refilled
    client._acquire_token(acquire_timeout=60)
    client._acquire_token(acquire_timeout=60)
    assert fake_clock.sleeps == []


def test_acquire_timeout_raises_instead_of_waiting(fake_redis, fake_clock):
    limits = RateLimit(capacity=1, refill_amount=1, refill_period_s=3600)  # very slow refill
    client = make_client(fake_redis, fake_clock, limits)

    client._acquire_token(acquire_timeout=3)
    with pytest.raises(ProviderRateLimitError, match="acquire timeout"):
        client._acquire_token(acquire_timeout=3)
    assert fake_clock.sleeps == []  # refused to start an unwinnable wait


def test_day_budget_hard_stop(fake_redis, fake_clock):
    limits = RateLimit(capacity=100, refill_amount=100, refill_period_s=1, day_budget=2)
    client = make_client(fake_redis, fake_clock, limits)

    client._check_budget()
    client._check_budget()
    with pytest.raises(BudgetExceeded, match="daily budget"):
        client._check_budget()


def test_month_budget_hard_stop(fake_redis, fake_clock):
    limits = RateLimit(capacity=100, refill_amount=100, refill_period_s=1, month_budget=1)
    client = make_client(fake_redis, fake_clock, limits)

    client._check_budget()
    with pytest.raises(BudgetExceeded, match="monthly budget"):
        client._check_budget()


def test_buckets_are_isolated_per_provider(fake_redis, fake_clock):
    limits = RateLimit(capacity=1, refill_amount=1, refill_period_s=3600)
    a = make_client(fake_redis, fake_clock, limits)
    b = RateLimitedClient(
        "otherprov",
        "https://api.other",
        fake_redis,
        limits,
        clock=fake_clock,
        sleep=fake_clock.sleep,
    )

    a._acquire_token(acquire_timeout=1)
    b._acquire_token(acquire_timeout=1)  # separate bucket, no contention
    assert fake_clock.sleeps == []


def test_budget_counter_stops_at_the_cap(fake_redis, fake_clock):
    """The counter is the Settings page's usage gauge, so a refused call must
    not inflate it. Production showed budget:tiingo:day = 4083 against a 900
    budget: the quote poller retried every 15s and each rejection still
    incremented, reporting 454% usage the provider was never asked for."""
    limits = RateLimit(capacity=100, refill_amount=100, refill_period_s=1, day_budget=2)
    client = make_client(fake_redis, fake_clock, limits)
    key = f"budget:testprov:day:{datetime.now(UTC):%Y%m%d}"

    client._check_budget()
    client._check_budget()
    for _ in range(10):
        with pytest.raises(BudgetExceeded):
            client._check_budget()

    assert int(fake_redis.get(key)) == 2  # never past the budget it was given


def test_budget_is_not_charged_when_the_bucket_refuses(fake_redis, fake_clock):
    """A call that dies waiting for a token was never made, so it must not be
    billed — otherwise a rate-limited job eats the daily budget of the job that
    runs after it."""
    limits = RateLimit(
        capacity=1, refill_amount=1, refill_period_s=3600, day_budget=100
    )
    client = make_client(fake_redis, fake_clock, limits)
    key = f"budget:testprov:day:{datetime.now(UTC):%Y%m%d}"

    client._assert_budget()
    client._acquire_token(acquire_timeout=3)
    client._charge_budget()
    assert int(fake_redis.get(key)) == 1

    client._assert_budget()  # budget is fine...
    with pytest.raises(ProviderRateLimitError):
        client._acquire_token(acquire_timeout=3)  # ...the bucket is not
    assert int(fake_redis.get(key)) == 1  # unchanged: no request was issued


def test_assert_budget_does_not_charge(fake_redis, fake_clock):
    limits = RateLimit(capacity=100, refill_amount=100, refill_period_s=1, day_budget=5)
    client = make_client(fake_redis, fake_clock, limits)
    key = f"budget:testprov:day:{datetime.now(UTC):%Y%m%d}"

    for _ in range(20):
        client._assert_budget()
    assert fake_redis.get(key) is None
