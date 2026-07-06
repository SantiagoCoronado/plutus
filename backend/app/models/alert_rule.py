from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

ALERT_CONDITIONS = ("above", "below")
ALERT_STATUSES = ("armed", "triggered", "disabled")


class AlertRule(Base):
    """A per-asset price threshold; the evaluator fires once on a crossing, then waits
    for an explicit re-arm (spec §Phase 7). `last_price` is the crossing-edge memory."""

    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    condition: Mapped[str] = mapped_column(Text)
    threshold: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    status: Mapped[str] = mapped_column(Text, default="armed", server_default="armed")
    # reserved for a future repeating mode; unused in the one-shot UI
    cooldown_minutes: Mapped[int | None] = mapped_column(Integer)
    # which side of the threshold the last observed quote sat on
    last_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("condition IN ('above','below')", name="ck_alert_rules_condition"),
        CheckConstraint("threshold > 0", name="ck_alert_rules_threshold"),
        CheckConstraint(
            "status IN ('armed','triggered','disabled')", name="ck_alert_rules_status"
        ),
        Index("ix_alert_rules_status_asset", "status", "asset_id"),
    )
