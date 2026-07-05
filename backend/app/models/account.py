from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

ACCOUNT_TYPES = ("brokerage", "exchange", "wallet", "bank", "manual")


class Account(Base):
    """A place money or assets live: an exchange, a wallet, a bank, a brokerage."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    type: Mapped[str] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text)
    # display default for the account; cash is tracked per (account, currency)
    currency: Mapped[str] = mapped_column(Text, default="USD", server_default="USD")
    note: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "type IN ('brokerage','exchange','wallet','bank','manual')",
            name="ck_accounts_type",
        ),
    )
