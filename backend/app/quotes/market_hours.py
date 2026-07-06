"""Trading-session gates for the poller. zoneinfo handles DST; there is no
holiday calendar (a documented gap)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")

_OPEN_MIN = 9 * 60 + 30  # 09:30
_CLOSE_MIN = 16 * 60  # 16:00


def us_market_open(now: datetime | None = None) -> bool:
    """Regular US cash session: weekdays 09:30–16:00 America/New_York.

    No holiday calendar (documented gap): on a US market holiday this returns
    True and the poller fetches a stale quote — harmless for a self-hosted hub,
    since get_quote caches for 60s and absorbs the wasted call.
    """
    ny = _to_zone(now, NY_TZ)
    if ny.weekday() >= 5:  # Sat/Sun
        return False
    minutes = ny.hour * 60 + ny.minute
    return _OPEN_MIN <= minutes < _CLOSE_MIN


def forex_open(now: datetime | None = None) -> bool:
    """FX trades ~24x5. We gate only on the weekend (Sat/Sun UTC); the intraweek
    Sunday-open / Friday-close boundaries aren't modeled (documented gap)."""
    return _to_zone(now, UTC_TZ).weekday() < 5


def _to_zone(now: datetime | None, tz: ZoneInfo) -> datetime:
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    return now.astimezone(tz)
