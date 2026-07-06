"""Phase 7 M5 unit: staleness verdicts across the cadence matrix, job folding
over hand-built run dicts, overall-status precedence, and budget pct math
(including missing counters) against fakeredis."""

from datetime import UTC, datetime, timedelta

import pytest

from app.health.aggregate import (
    DAILY,
    EXPECTED_CADENCE,
    QUARTER_HOURLY,
    WEEKLY,
    overall_status,
    provider_budgets,
    staleness_verdict,
    summarize_jobs,
)
from app.providers.base import PROVIDER_LIMITS

NOW = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)


def make_run(job_name, status="success", age=timedelta(hours=1), **overrides):
    run = {
        "job_name": job_name,
        "provider": "tiingo",
        "asset_class": "stock",
        "status": status,
        "started_at": NOW - age - timedelta(minutes=5),
        "finished_at": NOW - age,
        "rows_written": 100,
        "symbols_ok": 5,
        "symbols_failed": 0,
    }
    run.update(overrides)
    return run


class TestStalenessVerdict:
    @pytest.mark.parametrize(
        ("cadence", "age", "expected"),
        [
            # daily jobs: amber > 30h, red > 54h
            (DAILY, timedelta(hours=10), "green"),
            (DAILY, timedelta(hours=30), "green"),  # boundary is exclusive
            (DAILY, timedelta(hours=31), "amber"),
            (DAILY, timedelta(hours=54), "amber"),
            (DAILY, timedelta(hours=55), "red"),
            # weekly fundamentals: amber > 8d, red > 15d
            (WEEKLY, timedelta(days=7), "green"),
            (WEEKLY, timedelta(days=9), "amber"),
            (WEEKLY, timedelta(days=16), "red"),
            # 15-min news: amber > 2h, red > 12h
            (QUARTER_HOURLY, timedelta(minutes=30), "green"),
            (QUARTER_HOURLY, timedelta(hours=3), "amber"),
            (QUARTER_HOURLY, timedelta(hours=13), "red"),
        ],
    )
    def test_cadence_matrix(self, cadence, age, expected):
        assert staleness_verdict(NOW, cadence, NOW - age) == expected

    def test_no_success_on_record_is_amber(self):
        assert staleness_verdict(NOW, DAILY, None) == "amber"


class TestOverallStatus:
    def test_precedence(self):
        assert overall_status(["green", "green"]) == "green"
        assert overall_status(["green", "amber"]) == "amber"
        assert overall_status(["amber", "red", "green"]) == "red"

    def test_empty_is_green(self):
        assert overall_status([]) == "green"


class TestSummarizeJobs:
    def test_expected_job_never_seen_is_amber_with_note(self):
        jobs = {job["job_name"]: job for job in summarize_jobs([], NOW)}
        assert set(jobs) == set(EXPECTED_CADENCE)
        for job in jobs.values():
            assert job["staleness"] == "amber"
            assert job["note"] == "never ran"
            assert job["last_run_at"] is None

    def test_staleness_uses_last_success_not_last_run(self):
        # a fresh failure after a fresh success stays green; after a stale
        # success it goes red — the failed run never masks nor causes staleness
        runs = [
            make_run("news_pull", status="failed", age=timedelta(minutes=5), rows_written=0),
            make_run("news_pull", age=timedelta(hours=1)),
            make_run("eod_crypto", status="failed", age=timedelta(hours=1), rows_written=0),
            make_run("eod_crypto", age=timedelta(hours=60)),
        ]
        jobs = {job["job_name"]: job for job in summarize_jobs(runs, NOW)}
        assert jobs["news_pull"]["staleness"] == "green"
        assert jobs["news_pull"]["last_status"] == "failed"
        assert jobs["eod_crypto"]["staleness"] == "red"
        assert jobs["eod_crypto"]["last_success_at"] == NOW - timedelta(hours=60)

    def test_last_run_fields_come_from_newest_run(self):
        runs = [
            make_run("eod_stock", status="partial", age=timedelta(hours=2), rows_written=42),
            make_run("eod_stock", age=timedelta(hours=26), rows_written=500),
        ]
        jobs = {job["job_name"]: job for job in summarize_jobs(runs, NOW)}
        job = jobs["eod_stock"]
        assert job["last_status"] == "partial"
        assert job["rows_written"] == 42
        assert job["last_run_at"] == NOW - timedelta(hours=2)
        assert job["staleness"] == "green"  # success 26h ago, inside the 30h window

    def test_provider_falls_back_past_null(self):
        # a provider-not-configured failure records provider=None; the summary
        # still names the provider from the newest run that knew it
        runs = [
            make_run("eod_forex", status="failed", age=timedelta(hours=1), provider=None),
            make_run("eod_forex", age=timedelta(hours=25), provider="twelvedata"),
        ]
        jobs = {job["job_name"]: job for job in summarize_jobs(runs, NOW)}
        assert jobs["eod_forex"]["provider"] == "twelvedata"

    def test_ad_hoc_jobs_listed_after_expected_and_never_alarm(self):
        runs = [make_run("backfill", status="failed", age=timedelta(days=10))]
        jobs = summarize_jobs(runs, NOW)
        assert jobs[-1]["job_name"] == "backfill"
        assert jobs[-1]["staleness"] == "green"
        assert [job["job_name"] for job in jobs[:-1]] == list(EXPECTED_CADENCE)


class TestProviderBudgets:
    def test_pct_math_and_missing_counters(self, fake_redis):
        fake_redis.set(f"budget:tiingo:day:{NOW:%Y%m%d}", 450)
        fake_redis.set(f"budget:coingecko:month:{NOW:%Y%m}", 9001)

        budgets = {(b["provider"], b["window"]): b for b in provider_budgets(fake_redis, NOW)}

        tiingo = budgets[("tiingo", "day")]
        assert tiingo["used"] == 450
        assert tiingo["budget"] == 900
        assert tiingo["pct"] == 50.0

        # overrun counter reports past 100 — the UI clamps the bar, not the math
        assert budgets[("coingecko", "month")]["pct"] == pytest.approx(100.0, abs=0.1)

        # never-called provider: missing key reads as 0 used
        alphavantage = budgets[("alphavantage", "day")]
        assert alphavantage["used"] == 0
        assert alphavantage["pct"] == 0.0

    def test_covers_every_budgeted_provider_and_no_others(self, fake_redis):
        budgets = provider_budgets(fake_redis, NOW)
        expected = {
            (name, window)
            for name, limits in PROVIDER_LIMITS.items()
            for window, budget in (("day", limits.day_budget), ("month", limits.month_budget))
            if budget is not None
        }
        assert {(b["provider"], b["window"]) for b in budgets} == expected
