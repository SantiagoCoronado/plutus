from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScreenBacktestIn(BaseModel):
    """Backtest a screen: either a saved screen_id or an inline ast + asset_class."""

    screen_id: int | None = None
    ast: dict[str, Any] | None = None
    asset_class: Literal["stock", "etf", "crypto", "forex"] | None = None
    holding_days: int = Field(default=20, ge=1, le=126)
    start: date | None = None
    end: date | None = None
    benchmark: str = "SPY"
    fees_pct: float = Field(default=0.0, ge=0.0, le=0.05)

    @model_validator(mode="after")
    def _source(self):
        if (self.screen_id is None) == (self.ast is None):
            raise ValueError("provide exactly one of screen_id or ast")
        return self


class StrategyBacktestIn(BaseModel):
    asset_id: int
    entry: dict[str, Any]
    exit: dict[str, Any]
    stop_loss_pct: float | None = Field(default=None, gt=0.0, le=0.5)
    take_profit_pct: float | None = Field(default=None, gt=0.0, le=2.0)
    position_size_pct: float = Field(default=100.0, gt=0.0, le=100.0)
    cash: float = Field(default=100_000.0, gt=0.0)
    fees_pct: float = Field(default=0.0, ge=0.0, le=0.05)
    start: date | None = None
    end: date | None = None


class BacktestSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    status: str
    screen_id: int | None
    stats: dict[str, Any] | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class BacktestOut(BacktestSummaryOut):
    params: dict[str, Any]
    equity_curve: dict[str, Any] | None
    trade_list: list[Any] | None
    artifact_path: str | None
    started_at: datetime | None
