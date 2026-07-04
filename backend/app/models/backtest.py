from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

BACKTEST_KINDS = ("screen", "strategy")
BACKTEST_STATUSES = ("queued", "running", "done", "failed")


class Backtest(Base):
    """One backtest run (screen or strategy), executed by the worker and polled by the UI."""

    __tablename__ = "backtests"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="queued", server_default="queued")
    # kept when the run came from a saved screen; the AST itself is echoed into params
    screen_id: Mapped[int | None] = mapped_column(ForeignKey("screens.id", ondelete="SET NULL"))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # {"portfolio": [[iso_ts, value], ...], "benchmark": [...]} — downsampled to ≤500 points
    equity_curve: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # strategy: closed trades; screen: holdings log [{date, symbols}]
    trade_list: Mapped[list[Any] | None] = mapped_column(JSONB)
    artifact_path: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("kind IN ('screen','strategy')", name="ck_backtests_kind"),
        CheckConstraint(
            "status IN ('queued','running','done','failed')", name="ck_backtests_status"
        ),
        Index("ix_backtests_created_at", "created_at"),
    )
