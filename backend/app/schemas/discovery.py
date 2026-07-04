from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AssetClass = Literal["stock", "etf", "crypto", "forex"]
CandidateStatus = Literal["new", "reviewed", "starred", "dismissed"]
NotifyMode = Literal["off", "instant", "digest"]


class ClassUniverse(BaseModel):
    type: Literal["class"]


class WatchlistUniverse(BaseModel):
    type: Literal["watchlist"]
    watchlist_id: int


class MarketCapFloorUniverse(BaseModel):
    type: Literal["market_cap_floor"]
    min_market_cap: float = Field(gt=0)


class TopByMarketCapUniverse(BaseModel):
    type: Literal["top_by_market_cap"]
    count: int = Field(ge=1, le=100)


UniverseDef = Annotated[
    ClassUniverse | WatchlistUniverse | MarketCapFloorUniverse | TopByMarketCapUniverse,
    Field(discriminator="type"),
]


class MandateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    asset_class: AssetClass
    universe_def: UniverseDef
    rules: dict[str, Any] | None = None
    schedule: str
    score_weights: dict[str, float]
    min_score: float = Field(default=40.0, ge=0.0, le=100.0)
    notify_min_score: float | None = Field(default=None, ge=0.0, le=100.0)
    max_candidates: int = Field(default=20, ge=1, le=100)
    cooldown_days: int = Field(default=7, ge=0, le=90)
    notify: NotifyMode = "instant"
    active: bool = True


class MandatePatch(BaseModel):
    """List-page toggles; everything else goes through PUT."""

    active: bool | None = None
    notify: NotifyMode | None = None


class MandateStatsOut(BaseModel):
    candidates_total: int = 0
    new: int = 0
    starred: int = 0
    dismissed: int = 0
    # starred / (starred + dismissed); null until the user has voted
    hit_rate: float | None = None


class LastScanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    finished_at: datetime | None
    error: str | None


class MandateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    asset_class: str
    universe_def: dict[str, Any]
    rules: dict[str, Any] | None
    schedule: str
    score_weights: dict[str, float]
    min_score: float
    notify_min_score: float | None
    max_candidates: int
    cooldown_days: int
    notify: str
    active: bool
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # computed by the route, not stored
    next_run_at: datetime | None = None
    stats: MandateStatsOut | None = None
    last_scan: LastScanOut | None = None


class ScanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    mandate_id: int
    status: str
    stats: dict[str, Any] | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class SignalInfoOut(BaseModel):
    key: str
    label: str
    description: str
    asset_classes: list[str]
    needs_volume: bool
    supports_history_check: bool


class CandidateOut(BaseModel):
    id: int
    mandate_id: int
    mandate_name: str
    asset_id: int
    symbol: str
    name: str
    asset_class: str
    ts: datetime
    score: float
    status: str
    signals: list[dict[str, Any]]
    context: dict[str, Any]
    created_at: datetime


class CandidateStatusIn(BaseModel):
    status: CandidateStatus


class MandateCandidateSummaryOut(BaseModel):
    mandate_id: int
    mandate_name: str
    new: int = 0
    starred: int = 0
    dismissed: int = 0
    hit_rate: float | None = None


class CandidateSummaryOut(BaseModel):
    by_status: dict[str, int]
    by_mandate: list[MandateCandidateSummaryOut]
