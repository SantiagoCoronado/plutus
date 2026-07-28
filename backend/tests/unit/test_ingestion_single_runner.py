"""Regression cover for the duplicate-ingestion-run defect.

Symptom in production: eod_stock started at its 09:20 UTC beat slot and a second
run of the same task appeared at 10:20:35 — exactly the Kombu Redis default
visibility_timeout of 3600s later, because task_acks_late defers the ack until
the task finishes and the EOD job paces ~2.3h inside the Tiingo token bucket.
The two runs then split one 45-token/hour bucket, so the scheduled run abandoned
30+ symbols to ProviderRateLimitError while the duplicate abandoned 7.

Two guards are covered here: the broker never redelivers a live task, and a
redelivery that happens anyway is skipped by a lock. The third — a run that
loses its worker still closes its ingestion_runs row — needs the DB and lives in
tests/integration/test_ingestion_flow.py.
"""

import pytest

from worker import tasks
from worker.celery_app import BROKER_VISIBILITY_TIMEOUT_S, celery_app


class TestBrokerVisibilityTimeout:
    def test_exceeds_the_longest_task_time_limit(self):
        """The bug in one assertion: redelivery must not outrun the work."""
        assert BROKER_VISIBILITY_TIMEOUT_S > tasks.INGEST_LIMITS["time_limit"]

    def test_configured_on_the_broker_transport(self):
        options = celery_app.conf.broker_transport_options
        assert options["visibility_timeout"] == BROKER_VISIBILITY_TIMEOUT_S

    def test_acks_late_still_on(self):
        # The visibility timeout only matters because the ack is deferred; if
        # this ever flips, the timeout above stops being load-bearing.
        assert celery_app.conf.task_acks_late is True


class TestEodLock:
    """_eod_once must be a single runner per asset class."""

    @pytest.fixture(autouse=True)
    def _redis(self, fake_redis, monkeypatch):
        monkeypatch.setattr("app.providers.registry._shared_redis", lambda: fake_redis)
        return fake_redis

    def test_second_concurrent_run_is_skipped(self, monkeypatch):
        started: list[str] = []
        inner_result: list[int] = []

        def fake_run(asset_class):
            started.append(asset_class)
            # re-entrancy: a redelivered duplicate arriving mid-run is refused
            inner_result.append(tasks._eod_once(asset_class))
            return 42

        monkeypatch.setattr(tasks, "run_eod_ingestion", fake_run)

        assert tasks._eod_once("stock") == 42
        assert started == ["stock"]  # the nested call never reached the driver
        assert inner_result == [tasks.SKIPPED_LOCKED]

    def test_lock_is_released_for_the_next_run(self, monkeypatch):
        monkeypatch.setattr(tasks, "run_eod_ingestion", lambda _cls: 7)
        assert tasks._eod_once("stock") == 7
        assert tasks._eod_once("stock") == 7

    def test_lock_is_released_when_the_run_raises(self, monkeypatch):
        def boom(_cls):
            raise RuntimeError("worker died")

        monkeypatch.setattr(tasks, "run_eod_ingestion", boom)
        with pytest.raises(RuntimeError):
            tasks._eod_once("stock")

        monkeypatch.setattr(tasks, "run_eod_ingestion", lambda _cls: 7)
        assert tasks._eod_once("stock") == 7  # not wedged behind a stuck lock

    def test_classes_do_not_block_each_other(self, monkeypatch):
        seen: list[str] = []

        def fake_run(asset_class):
            seen.append(asset_class)
            # crypto running must not stop stock from starting
            if asset_class == "crypto":
                seen.append(f"stock->{tasks._eod_once('stock')}")
            return 1

        monkeypatch.setattr(tasks, "run_eod_ingestion", fake_run)
        tasks._eod_once("crypto")
        assert seen == ["crypto", "stock", "stock->1"]

    def test_ingest_eod_all_skips_only_the_locked_class(self, monkeypatch, fake_redis):
        monkeypatch.setattr(tasks, "run_eod_ingestion", lambda _cls: 1)
        fake_redis.set("lock:ingest:eod:stock", "held-by-the-nightly-beat", ex=600)

        results = tasks.ingest_eod_all.run()
        assert results == [1, 1, tasks.SKIPPED_LOCKED]
