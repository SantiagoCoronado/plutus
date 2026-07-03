import math
from typing import Any

import pandas as pd

from app.providers.base import CANDLE_COLUMNS
from app.schemas.common import Interval


def candles_to_rows(df: pd.DataFrame, asset_id: int, interval: Interval) -> list[dict[str, Any]]:
    """Canonical provider DataFrame -> ohlcv row dicts ready for upsert.

    Owns the timestamp convention: ts must be tz-aware UTC, normalized to midnight
    for daily bars. Misaligned timestamps here would silently break Phase 2
    indicator joins, so it is enforced, not assumed.
    """
    if df.empty:
        return []
    missing = [c for c in CANDLE_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"candles frame missing columns: {missing}")

    ts = pd.to_datetime(df["ts"], utc=True)
    if interval in (Interval.d1, Interval.w1):
        ts = ts.dt.normalize()

    out = df.assign(ts=ts).dropna(subset=["open", "high", "low", "close"])
    rows: list[dict[str, Any]] = []
    for record in out[CANDLE_COLUMNS].to_dict("records"):
        volume = record["volume"]
        if volume is not None and (isinstance(volume, float) and math.isnan(volume)):
            volume = None
        rows.append(
            {
                "asset_id": asset_id,
                "interval": interval.value,
                "ts": record["ts"].to_pydatetime(),
                "open": float(record["open"]),
                "high": float(record["high"]),
                "low": float(record["low"]),
                "close": float(record["close"]),
                "volume": None if volume is None else float(volume),
            }
        )
    return rows
