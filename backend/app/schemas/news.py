from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NewsItemIn(BaseModel):
    """Provider-neutral news item (adapter output)."""

    ts: datetime
    source: str
    headline: str
    url: str


class NewsItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: datetime
    source: str
    headline: str
    url: str
    tickers: list[str]
    sentiment: float | None
