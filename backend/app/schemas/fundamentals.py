from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class FundamentalsPeriod(BaseModel):
    """Provider-neutral transfer object (FMP / EDGAR adapters both emit this)."""

    period: str = "annual"
    report_date: date
    fiscal_year: int | None = None
    currency: str = "USD"
    revenue: float | None = None
    eps: float | None = None
    fcf: float | None = None
    gross_margin: float | None = None
    net_margin: float | None = None
    roe: float | None = None
    debt_to_equity: float | None = None
    pe: float | None = None
    ps: float | None = None
    ev_ebitda: float | None = None
    metrics: dict = Field(default_factory=dict)


class FundamentalsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    asset_id: int
    period: str
    report_date: date
    fiscal_year: int | None
    currency: str
    provider: str
    fetched_at: datetime
    revenue: float | None
    eps: float | None
    fcf: float | None
    gross_margin: float | None
    net_margin: float | None
    roe: float | None
    debt_to_equity: float | None
    pe: float | None
    ps: float | None
    ev_ebitda: float | None
    metrics: dict
