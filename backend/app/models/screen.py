from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Screen(Base):
    """A saved screener query: a filter AST evaluated against asset_metrics."""

    __tablename__ = "screens"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    # NULL = screen runs across all asset classes
    asset_class: Mapped[str | None] = mapped_column(Text)
    ast: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "asset_class IN ('stock','etf','crypto','forex')", name="ck_screens_asset_class"
        ),
    )
