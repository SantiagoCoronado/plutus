from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ScreenIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    asset_class: Literal["stock", "etf", "crypto", "forex"] | None = None
    ast: dict[str, Any]


class ScreenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    asset_class: str | None
    ast: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ScreenFieldOut(BaseModel):
    name: str
    backtestable: bool
    fundamental: bool


class ScreenRunRequest(BaseModel):
    ast: dict[str, Any]
    asset_class: Literal["stock", "etf", "crypto", "forex"] | None = None
    limit: int = Field(default=200, ge=1, le=200)


class ScreenHitOut(BaseModel):
    asset_id: int
    symbol: str
    name: str
    asset_class: str
    as_of: date | None
    values: dict[str, float | None]


class ScreenRunResult(BaseModel):
    count: int
    columns: list[str]
    results: list[ScreenHitOut]
