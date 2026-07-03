from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import Interval


class Candle(BaseModel):
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


class OhlcvResponse(BaseModel):
    asset_id: int
    interval: Interval
    candles: list[Candle]
