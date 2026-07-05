from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TranslationIn(BaseModel):
    content: str = Field(min_length=20, max_length=120_000)
    symbol: str | None = Field(default=None, max_length=20)


class TranslationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    translatable: bool | None
    symbol: str | None
    asset_id: int | None
    understanding_md: str | None
    limitations: list[Any] | None
    spec: dict[str, Any] | None
    backtest_id: int | None
    provider: str | None
    model: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class TranslationConfirmOut(BaseModel):
    translation_id: int
    backtest_id: int
