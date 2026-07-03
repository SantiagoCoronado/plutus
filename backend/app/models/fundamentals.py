from datetime import date, datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Spec §4.2's normalized key metrics; raw statement payloads live in `metrics` jsonb
FUNDAMENTAL_COLUMNS: tuple[str, ...] = (
    "revenue", "eps", "fcf", "gross_margin", "net_margin",
    "roe", "debt_to_equity", "pe", "ps", "ev_ebitda",
)  # fmt: skip


class Fundamentals(Base):
    __tablename__ = "fundamentals"

    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True
    )
    period: Mapped[str] = mapped_column(Text, primary_key=True)
    report_date: Mapped[date] = mapped_column(primary_key=True)
    fiscal_year: Mapped[int | None]
    currency: Mapped[str] = mapped_column(Text, default="USD", server_default="USD")
    provider: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    revenue: Mapped[float | None]
    eps: Mapped[float | None]
    fcf: Mapped[float | None]
    gross_margin: Mapped[float | None]
    net_margin: Mapped[float | None]
    roe: Mapped[float | None]
    debt_to_equity: Mapped[float | None]
    pe: Mapped[float | None]
    ps: Mapped[float | None]
    ev_ebitda: Mapped[float | None]

    # {"income": {...}, "balance": {...}, "cashflow": {...}, "ratios": {...}, "key_metrics": {...}}
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))

    __table_args__ = (
        CheckConstraint("period IN ('annual','quarter','ttm')", name="period_valid"),
    )
