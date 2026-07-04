"""Cron parsing + due logic across the local-timezone day boundary.

App timezone is America/Mexico_City (UTC-6, no DST since 2022); all datetimes
below are expressed in UTC, with local times noted in comments.
"""

from datetime import UTC, datetime

import pytest

from app.discovery.schedule import is_due, next_run_at, parse_cron

WEEKDAYS_0730 = "30 7 * * 1-5"  # 07:30 local, Monday-Friday

FRI_0731_LOCAL = datetime(2026, 7, 3, 13, 31, tzinfo=UTC)
FRI_0800_LOCAL = datetime(2026, 7, 3, 14, 0, tzinfo=UTC)
SAT_0800_LOCAL = datetime(2026, 7, 4, 14, 0, tzinfo=UTC)
MON_0730_LOCAL = datetime(2026, 7, 6, 13, 30, tzinfo=UTC)


def test_reference_dates_are_the_weekdays_the_test_assumes():
    assert FRI_0731_LOCAL.weekday() == 4
    assert SAT_0800_LOCAL.weekday() == 5
    assert MON_0730_LOCAL.weekday() == 0


def test_parse_cron_accepts_standard_specs():
    parse_cron("30 7 * * 1-5")
    parse_cron("*/5 * * * *")
    parse_cron("0 8 1 * *")


@pytest.mark.parametrize(
    "expr",
    [
        "30 7 * *",  # 4 fields
        "30 7 * * 1 extra",  # 6 fields
        "61 * * * *",  # minute out of range
        "* banana * * *",  # non-numeric hour
        "",
    ],
)
def test_parse_cron_rejects_bad_specs(expr):
    with pytest.raises(ValueError, match="schedule"):
        parse_cron(expr)


def test_due_when_todays_fire_time_has_passed():
    thu_0731_local = datetime(2026, 7, 2, 13, 31, tzinfo=UTC)
    assert is_due(WEEKDAYS_0730, thu_0731_local, nowfun=lambda: FRI_0800_LOCAL)


def test_not_due_after_running_today():
    assert not is_due(WEEKDAYS_0730, FRI_0731_LOCAL, nowfun=lambda: FRI_0800_LOCAL)


def test_weekday_schedule_skips_the_weekend():
    assert not is_due(WEEKDAYS_0730, FRI_0731_LOCAL, nowfun=lambda: SAT_0800_LOCAL)


def test_next_run_at_lands_on_monday_morning():
    eta = next_run_at(WEEKDAYS_0730, FRI_0731_LOCAL, nowfun=lambda: SAT_0800_LOCAL)
    assert eta == MON_0730_LOCAL


def test_next_run_at_clamps_overdue_to_now():
    thu_0700_local = datetime(2026, 7, 2, 13, 0, tzinfo=UTC)  # before Thursday's fire
    eta = next_run_at(WEEKDAYS_0730, thu_0700_local, nowfun=lambda: FRI_0800_LOCAL)
    assert eta == FRI_0800_LOCAL  # overdue -> "now", the dispatcher fires within 5 min


def test_every_five_minutes_due_quickly():
    ten_past = datetime(2026, 7, 3, 14, 10, tzinfo=UTC)
    assert is_due("*/5 * * * *", FRI_0800_LOCAL, nowfun=lambda: ten_past)
