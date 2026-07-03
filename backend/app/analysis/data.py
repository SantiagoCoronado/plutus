from datetime import UTC, date, datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Ohlcv

FRAME_COLUMNS = ["open", "high", "low", "close", "volume"]

# 420 daily bars ≈ sma_200 + 52w metrics + margin
DEFAULT_LOOKBACK_DAYS = 420


def load_ohlcv_frame(
    session: Session,
    asset_id: int,
    interval: str = "1d",
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    """OHLCV frame with an ascending tz-aware UTC DatetimeIndex."""
    query = select(
        Ohlcv.ts, Ohlcv.open, Ohlcv.high, Ohlcv.low, Ohlcv.close, Ohlcv.volume
    ).where(Ohlcv.asset_id == asset_id, Ohlcv.interval == interval)
    if start is not None:
        query = query.where(Ohlcv.ts >= start)
    elif lookback_days:
        horizon = datetime.now(UTC) - timedelta(days=lookback_days)
        query = query.where(Ohlcv.ts >= horizon)
    if end is not None:
        query = query.where(Ohlcv.ts <= end)
    rows = session.execute(query.order_by(Ohlcv.ts)).all()
    if not rows:
        return pd.DataFrame(columns=FRAME_COLUMNS, index=pd.DatetimeIndex([], tz=UTC))
    df = pd.DataFrame(rows, columns=["ts", *FRAME_COLUMNS])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")
