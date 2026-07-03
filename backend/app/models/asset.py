from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

ASSET_CLASSES = ("stock", "etf", "crypto", "forex")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    asset_class: Mapped[str] = mapped_column(Text)
    exchange: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text, default="USD")
    # spec column name is `metadata`, which collides with SQLAlchemy's Declarative attribute
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, server_default=text("'{}'"))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("symbol", "asset_class", name="uq_assets_symbol_asset_class"),
        CheckConstraint(
            "asset_class IN ('stock','etf','crypto','forex')", name="asset_class_valid"
        ),
        Index("ix_assets_asset_class", "asset_class"),
    )

    @property
    def provider_symbol_map(self) -> dict[str, str]:
        return (self.meta or {}).get("provider_symbols", {})
