from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WatchlistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class WatchlistItemAdd(BaseModel):
    asset_id: int


class WatchlistItemOut(BaseModel):
    asset_id: int
    symbol: str
    name: str
    asset_class: str
    added_at: datetime
    close: float | None = None
    return_1d: float | None = None


class WatchlistOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    created_at: datetime
    items: list[WatchlistItemOut] = Field(default_factory=list)
