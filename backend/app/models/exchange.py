from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

EXCHANGE_PROVIDERS = ("bitso",)
EXCHANGE_SYNC_STATUSES = ("running", "success", "partial", "failed")


class ExchangeLink(Base):
    """Sync cursor for a read-only exchange connection; one per exchange account.

    API keys live encrypted in app_settings, never here — this row only remembers
    where the last sync stopped so the next run resumes idempotently.
    """

    __tablename__ = "exchange_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), unique=True
    )
    provider: Mapped[str] = mapped_column(Text)
    last_trade_tid: Mapped[str | None] = mapped_column(Text)
    last_funding_id: Mapped[str | None] = mapped_column(Text)
    last_withdrawal_id: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("provider IN ('bitso')", name="ck_exchange_links_provider"),
    )


class ExchangeSyncRun(Base):
    """Audit of one exchange sync pass — mirrors ingestion_runs (open/close pattern)."""

    __tablename__ = "exchange_sync_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text)
    trades_created: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    trades_skipped: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # {skipped_unknown_symbols, error}
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        CheckConstraint(
            "status IN ('running','success','partial','failed')",
            name="ck_exchange_sync_runs_status",
        ),
        Index(
            "ix_exchange_sync_runs_account_started",
            "account_id",
            text("started_at DESC"),
        ),
    )
