from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

TRANSLATION_STATUSES = ("draft", "confirmed", "discarded", "failed")


class StrategyTranslation(Base):
    """A strategy translated from pasted content, with its fidelity report (spec §13.5).

    Provenance is the point: the source content, the plain-English understanding,
    and the list of everything that could NOT be expressed are stored alongside
    the machine spec. Confirming a draft is the only path to a backtest run —
    a silent approximation is a bug.
    """

    __tablename__ = "strategy_translations"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="SET NULL")
    )
    source_content: Mapped[str] = mapped_column(Text)
    # "buy when X, sell when Y, exit at Z" — shown to the user before anything runs
    understanding_md: Mapped[str | None] = mapped_column(Text)
    # ["source uses 4-hour bars; this backtest is daily-only", ...]
    limitations: Mapped[list[Any] | None] = mapped_column(JSONB)
    # StrategyBacktestIn-shaped: {entry, exit, stop_loss_pct, take_profit_pct, ...}
    spec: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    symbol: Mapped[str | None] = mapped_column(Text)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"))
    # the model may declare the content untranslatable (e.g. options legs only)
    translatable: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(Text, default="draft", server_default="draft")
    backtest_id: Mapped[int | None] = mapped_column(
        ForeignKey("backtests.id", ondelete="SET NULL")
    )
    provider: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','confirmed','discarded','failed')",
            name="ck_strategy_translations_status",
        ),
        Index("ix_strategy_translations_created", "created_at"),
    )
