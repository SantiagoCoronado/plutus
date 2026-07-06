"""Dashboard aggregate (spec §9.1) — one payload for the command-center page.

Percentage conventions, kept deliberately explicit because two live side by side:
  * day_pnl_pct / twr_pct / benchmark_return_pct are FRACTIONS (0.0123 = +1.23%),
    matching the rest of the app (unrealized_pnl_pct, PerformanceReport.twr) — the
    frontend runs them through fmtPct.
  * HeatmapTile.change_pct is a PERCENT number (1.23 = +1.23%), matching the WS
    tick's change_pct so a 1D tile can be live-recolored without conversion, and
    so the ±3% diverging clamp reads in the same units.
"""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel

AssetClass = Literal["stock", "etf", "crypto", "forex"]
CandidateStatus = Literal["new", "reviewed", "starred", "dismissed"]
HealthLight = Literal["green", "amber", "red"]


class ValuePoint(BaseModel):
    date: date
    value: float


class PortfolioBlock(BaseModel):
    value: float | None
    currency: str
    day_pnl: float | None
    day_pnl_pct: float | None  # fraction
    series_30d: list[ValuePoint]


class YtdBlock(BaseModel):
    twr_pct: float | None  # fraction
    benchmark_symbol: str
    benchmark_return_pct: float | None  # fraction


class DashboardCandidate(BaseModel):
    id: int
    asset_id: int
    symbol: str
    name: str | None
    asset_class: AssetClass
    mandate_name: str
    score: float
    status: CandidateStatus
    signals_summary: list[str]


class CandidatesBlock(BaseModel):
    new_count: int
    top: list[DashboardCandidate]


class AgentBrief(BaseModel):
    subject: str
    body: str | None
    sent_at: datetime
    meta: dict[str, Any]


class MarketStripEntry(BaseModel):
    label: str
    symbol: str
    asset_class: str


class DashboardOut(BaseModel):
    portfolio: PortfolioBlock
    ytd: YtdBlock
    candidates: CandidatesBlock
    last_scan_at: datetime | None
    agent_brief: AgentBrief | None
    ingestion_status: HealthLight
    armed_alerts: int
    market_strip: list[MarketStripEntry]


class HeatmapTile(BaseModel):
    symbol: str
    asset_id: int
    name: str | None
    asset_class: AssetClass
    sector: str | None
    size: float
    change_pct: float  # PERCENT (1.23 = +1.23%); 0.0 when the metric is missing
    price: float | None
    weight_pct: float | None  # tile's share of the treemap, percent
    pnl: float | None  # unrealized P&L, portfolio mode only


class HeatmapOut(BaseModel):
    mode: Literal["portfolio", "watchlist", "market"]
    timeframe: Literal["1D", "1W", "1M", "YTD"]
    currency: str
    tiles: list[HeatmapTile]
