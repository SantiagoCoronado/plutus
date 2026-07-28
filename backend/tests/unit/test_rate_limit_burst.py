"""A token bucket's capacity is its BURST, not its rate.

Every PROVIDER_LIMITS entry once had capacity == refill_amount, which lets the
bucket issue capacity + refill_amount in the first refill period — twice the
intended rate. Against Tiingo's 50/hour cap the nightly EOD job issued 51 calls
in its first ten minutes, was refused with HTTP 429 for the remaining fifty, and
resumed the moment the clock hour rolled over:

    06:10   51 attempts   1 failure
    06:20   8 attempts    8 failures     <- cap hit, everything refused
    06:30   7 attempts    7 failures
    06:40   8 attempts    8 failures
    06:50   7 attempts    7 failures
    07:00   8 attempts    0 failures     <- provider window reset
"""

import pytest

from app.providers.base import PROVIDER_LIMITS, RateLimit
from app.providers.http import DEFAULT_ACQUIRE_TIMEOUT_S

# What each provider actually publishes, per its own refill period. Duplicated
# from the PROVIDER_LIMITS comment on purpose: if someone edits a limit without
# updating the documented cap, these disagree and the test says so.
PUBLISHED = {
    "tiingo": 50,
    "coingecko": 30,
    "twelvedata": 8,
    "finnhub": 60,
    "alphavantage": 5,
    "binance": 3000,
    "fmp": 8,
    "edgar": 10,
    "bitso": 60,
}


class TestWorstCaseStaysUnderPublished:
    @pytest.mark.parametrize("provider", sorted(PROVIDER_LIMITS))
    def test_burst_plus_refill_within_published_cap(self, provider):
        limits = PROVIDER_LIMITS[provider]
        assert limits.worst_case_per_period <= PUBLISHED[provider], (
            f"{provider} can issue {limits.worst_case_per_period} in one period "
            f"against a published {PUBLISHED[provider]}"
        )

    @pytest.mark.parametrize("provider", sorted(PROVIDER_LIMITS))
    def test_declared_cap_matches_the_documented_one(self, provider):
        assert PROVIDER_LIMITS[provider].published_per_period == PUBLISHED[provider]

    def test_every_provider_declares_its_cap(self):
        # __post_init__ only guards entries that declare one, so none may opt out
        missing = [p for p, lim in PROVIDER_LIMITS.items() if lim.published_per_period is None]
        assert missing == []

    @pytest.mark.parametrize("provider", sorted(PROVIDER_LIMITS))
    def test_capacity_is_a_burst_not_the_whole_allowance(self, provider):
        # the specific shape of the bug: a bucket whose burst equals its rate
        limits = PROVIDER_LIMITS[provider]
        assert limits.capacity < limits.refill_amount, (
            f"{provider}'s capacity is its entire refill allowance — that is the "
            "double-rate bug, not a burst"
        )


class TestPacingStaysWithinAcquireTimeout:
    """The steady-state gap between tokens must fit the default acquire timeout.

    Once the burst is spent a batch job waits one full token interval per
    symbol. If that interval exceeded the acquire timeout, _acquire_token would
    refuse immediately and *every* symbol after the burst would fail — turning a
    slow job into a broken one. Tiingo is the tight one at 90s against 120s.
    """

    @pytest.mark.parametrize("provider", sorted(PROVIDER_LIMITS))
    def test_token_interval_fits_the_default_timeout(self, provider):
        limits = PROVIDER_LIMITS[provider]
        assert limits.seconds_per_token < DEFAULT_ACQUIRE_TIMEOUT_S, (
            f"{provider} paces at {limits.seconds_per_token:.0f}s/token but the "
            f"default acquire timeout is {DEFAULT_ACQUIRE_TIMEOUT_S}s — every "
            "call after the burst would be refused"
        )


class TestPostInitGuard:
    def test_rejects_a_bucket_that_exceeds_its_published_cap(self):
        with pytest.raises(ValueError, match="lower `capacity`"):
            RateLimit(
                capacity=45, refill_amount=45, refill_period_s=3600, published_per_period=50
            )

    def test_accepts_a_bucket_exactly_at_the_cap(self):
        limits = RateLimit(
            capacity=5, refill_amount=45, refill_period_s=3600, published_per_period=50
        )
        assert limits.worst_case_per_period == 50

    def test_undeclared_cap_is_not_validated(self):
        # keeps ad-hoc RateLimits in tests (WIDE_OPEN etc.) constructible
        assert RateLimit(capacity=1000, refill_amount=1000, refill_period_s=1)


class TestEodRunFitsItsWindow:
    """Slowing the burst must not push the nightly run past its task limit."""

    STOCK_UNIVERSE = 105  # active stock + etf assets

    def test_full_stock_run_fits_the_task_time_limit(self):
        from worker.tasks import INGEST_LIMITS

        limits = PROVIDER_LIMITS["tiingo"]
        paced = self.STOCK_UNIVERSE - limits.capacity
        seconds = paced * limits.seconds_per_token
        assert seconds < INGEST_LIMITS["soft_time_limit"], (
            f"a {self.STOCK_UNIVERSE}-symbol run needs {seconds / 3600:.1f}h but the "
            f"soft limit is {INGEST_LIMITS['soft_time_limit'] / 3600:.1f}h"
        )

    def test_full_stock_run_stays_inside_the_daily_budget(self):
        assert self.STOCK_UNIVERSE <= PROVIDER_LIMITS["tiingo"].day_budget
