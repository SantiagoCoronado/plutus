from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

BANK_INVESTMENT_KINDS = ("demand", "fixed_term")
DAY_COUNTS = ("act360", "act365")
COMPOUNDING_MODES = ("daily", "monthly", "at_maturity")
INVESTMENT_STATUSES = ("active", "matured", "closed")


class BankInvestment(Base):
    """A fixed-rate bank product: a term deposit (pagaré/CETES-style) or an
    interest-bearing demand balance. Bookkeeping plus interest math — never
    connected to a bank."""

    __tablename__ = "bank_investments"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    principal: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    currency: Mapped[str] = mapped_column(Text, default="MXN", server_default="MXN")
    # decimal fraction: 0.105 means 10.5% per year
    annual_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6))
    # [{"up_to": 25000, "annual_rate": 0.15}, {"up_to": null, "annual_rate": 0.05}]
    rate_tiers: Mapped[list[Any] | None] = mapped_column(JSONB)
    day_count: Mapped[str] = mapped_column(Text, default="act360", server_default="act360")
    compounding: Mapped[str] = mapped_column(
        Text, default="at_maturity", server_default="at_maturity"
    )
    start_date: Mapped[date] = mapped_column(Date)
    term_days: Mapped[int | None] = mapped_column(Integer)
    # derived start_date + term_days; stored so maturity queries stay indexable
    maturity_date: Mapped[date | None] = mapped_column(Date)
    cap_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    status: Mapped[str] = mapped_column(Text, default="active", server_default="active")
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("kind IN ('demand','fixed_term')", name="ck_bank_investments_kind"),
        CheckConstraint("principal > 0", name="ck_bank_investments_principal"),
        CheckConstraint("annual_rate >= 0", name="ck_bank_investments_annual_rate"),
        CheckConstraint("day_count IN ('act360','act365')", name="ck_bank_investments_day_count"),
        CheckConstraint(
            "compounding IN ('daily','monthly','at_maturity')",
            name="ck_bank_investments_compounding",
        ),
        CheckConstraint(
            "term_days IS NULL OR term_days > 0", name="ck_bank_investments_term_days"
        ),
        CheckConstraint(
            "kind = 'demand' OR term_days IS NOT NULL",
            name="ck_bank_investments_term_required",
        ),
        CheckConstraint(
            "status IN ('active','matured','closed')", name="ck_bank_investments_status"
        ),
        Index("ix_bank_investments_status_maturity", "status", "maturity_date"),
    )
