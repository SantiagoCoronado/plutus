from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_name: Mapped[str] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text)
    asset_class: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, default="running")
    rows_written: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    symbols_ok: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    symbols_failed: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    details: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))

    __table_args__ = (
        CheckConstraint(
            "status IN ('running','success','partial','failed')", name="status_valid"
        ),
        Index("ix_ingestion_runs_started_at", started_at.desc()),
    )
