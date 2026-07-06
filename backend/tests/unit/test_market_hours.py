"""US market-hours + forex-weekend gates. Boundaries are exact-minute; DST is
exercised by comparing the same UTC instant in winter (EST) and summer (EDT)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.quotes.market_hours import forex_open, us_market_open

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# 2026-07-08 is a Wednesday; 2026-07-04 Saturday; 2026-07-05 Sunday.
WED = (2026, 7, 8)
SAT = (2026, 7, 4)
SUN = (2026, 7, 5)


def _ny(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=NY)


class TestUsMarketOpen:
    def test_before_open_closed(self):
        assert us_market_open(_ny(*WED, 9, 29)) is False

    def test_at_open_boundary_open(self):
        assert us_market_open(_ny(*WED, 9, 30)) is True

    def test_last_minute_open(self):
        assert us_market_open(_ny(*WED, 15, 59)) is True

    def test_at_close_boundary_closed(self):
        assert us_market_open(_ny(*WED, 16, 0)) is False

    def test_midday_open(self):
        assert us_market_open(_ny(*WED, 12, 0)) is True

    def test_saturday_closed(self):
        assert us_market_open(_ny(*SAT, 12, 0)) is False

    def test_sunday_closed(self):
        assert us_market_open(_ny(*SUN, 12, 0)) is False

    def test_dst_summer_open(self):
        # 13:30 UTC in July (EDT, UTC-4) == 09:30 ET -> open
        assert us_market_open(datetime(2026, 7, 8, 13, 30, tzinfo=UTC)) is True

    def test_dst_winter_same_utc_is_closed(self):
        # 13:30 UTC in January (EST, UTC-5) == 08:30 ET -> before open
        assert us_market_open(datetime(2026, 1, 7, 13, 30, tzinfo=UTC)) is False

    def test_dst_winter_open(self):
        # 14:30 UTC in January (EST) == 09:30 ET -> open
        assert us_market_open(datetime(2026, 1, 7, 14, 30, tzinfo=UTC)) is True

    def test_naive_treated_as_ny_local(self):
        assert us_market_open(datetime(2026, 7, 8, 10, 0)) is True

    def test_none_returns_bool(self):
        assert isinstance(us_market_open(), bool)


class TestForexOpen:
    def test_weekday_open(self):
        assert forex_open(datetime(*WED, 3, 0, tzinfo=UTC)) is True

    def test_saturday_closed(self):
        assert forex_open(datetime(*SAT, 12, 0, tzinfo=UTC)) is False

    def test_sunday_closed(self):
        assert forex_open(datetime(*SUN, 12, 0, tzinfo=UTC)) is False

    def test_none_returns_bool(self):
        assert isinstance(forex_open(), bool)
