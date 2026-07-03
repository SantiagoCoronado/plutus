"""1W/1M bars resampled on read from daily data (never stored).

Server-side via Timescale time_bucket + first()/last() ordered aggregates:
one source of truth with the indicator engine, calendar-correct months, and the
client stays a dumb renderer. The trailing bucket includes the in-progress
week/month — standard chart behavior.
"""

from datetime import UTC, date

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.analysis.data import FRAME_COLUMNS

BUCKET_WIDTHS = {"1w": "7 days", "1M": "1 month"}

_RESAMPLE_SQL = sa.text(
    """
    SELECT time_bucket(CAST(:width AS interval), ts) AS bucket,
           first(open, ts) AS open,
           max(high) AS high,
           min(low) AS low,
           last(close, ts) AS close,
           sum(volume) AS volume
    FROM ohlcv
    WHERE asset_id = :asset_id
      AND interval = '1d'
      AND (CAST(:start AS date) IS NULL OR ts >= CAST(:start AS date))
      AND (CAST(:end AS date) IS NULL OR ts <= CAST(:end AS date))
    GROUP BY bucket
    ORDER BY bucket
    LIMIT :limit
    """
)


def resample_rows(
    session: Session,
    asset_id: int,
    interval: str,
    start: date | None = None,
    end: date | None = None,
    limit: int = 10_000,
) -> list:
    width = BUCKET_WIDTHS.get(interval)
    if width is None:
        raise ValueError(f"unsupported resample interval {interval!r}")
    return session.execute(
        _RESAMPLE_SQL,
        {"width": width, "asset_id": asset_id, "start": start, "end": end, "limit": limit},
    ).all()


def resample_frame(
    session: Session,
    asset_id: int,
    interval: str,
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    """Same buckets as resample_rows, shaped like data.load_ohlcv_frame's output."""
    rows = resample_rows(session, asset_id, interval, start, end)
    if not rows:
        return pd.DataFrame(columns=FRAME_COLUMNS, index=pd.DatetimeIndex([], tz=UTC))
    df = pd.DataFrame(rows, columns=["ts", *FRAME_COLUMNS])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")
