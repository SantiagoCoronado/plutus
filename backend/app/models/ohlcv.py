from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d", "1w")


class Ohlcv(Base):
    """TimescaleDB hypertable (created in migration 0001); Phase 1 writes only '1d'."""

    __tablename__ = "ohlcv"

    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True
    )
    interval: Mapped[str] = mapped_column(Text, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[float]
    high: Mapped[float]
    low: Mapped[float]
    close: Mapped[float]
    volume: Mapped[float | None]

    __table_args__ = (
        CheckConstraint(
            "interval IN ('1m','5m','15m','1h','4h','1d','1w')", name="interval_valid"
        ),
    )
