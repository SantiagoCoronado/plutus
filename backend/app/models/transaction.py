from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

TRANSACTION_TYPES = (
    "buy",
    "sell",
    "deposit",
    "withdrawal",
    "dividend",
    "interest",
    "fee",
    "transfer_in",
    "transfer_out",
)
# these move asset units and must reference an asset
ASSET_TRANSACTION_TYPES = ("buy", "sell", "transfer_in", "transfer_out", "dividend")


class Transaction(Base):
    """The portfolio's source of truth — positions and lots are derived, never stored."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"))
    type: Mapped[str] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # asset units for buy/sell/transfer_*; cash amount for the money-only types
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    # per-unit price in `currency`; NULL for money-only types
    price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    fees: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0, server_default="0")
    # currency of the cash leg (a Bitso stock buy is MXN against a USD-quoted asset)
    currency: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    # dedup key for CSV imports; unique per account when present
    external_id: Mapped[str | None] = mapped_column(Text)
    # specific-ID sells: [{"buy_transaction_id": int, "quantity": num}]
    lot_links: Mapped[list[Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "type IN ('buy','sell','deposit','withdrawal','dividend','interest',"
            "'fee','transfer_in','transfer_out')",
            name="ck_transactions_type",
        ),
        CheckConstraint("quantity > 0", name="ck_transactions_quantity"),
        CheckConstraint("price IS NULL OR price >= 0", name="ck_transactions_price"),
        CheckConstraint("fees >= 0", name="ck_transactions_fees"),
        CheckConstraint(
            "type NOT IN ('buy','sell','transfer_in','transfer_out','dividend')"
            " OR asset_id IS NOT NULL",
            name="ck_transactions_asset_required",
        ),
        Index("ix_transactions_account_ts", "account_id", "ts"),
        Index("ix_transactions_asset_ts", "asset_id", "ts"),
        Index(
            "uq_transactions_account_external",
            "account_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
    )
