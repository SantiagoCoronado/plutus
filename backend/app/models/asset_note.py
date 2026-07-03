from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AssetNote(Base):
    __tablename__ = "asset_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    title: Mapped[str | None] = mapped_column(Text)
    body_md: Mapped[str] = mapped_column(Text)
    # 'ai' reserved for Phase 7's write_research_note ("tagged as AI-generated", spec §13.2)
    source: Mapped[str] = mapped_column(Text, default="user", server_default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("source IN ('user','ai')", name="source_valid"),
        Index("ix_asset_notes_asset_id", "asset_id"),
    )
