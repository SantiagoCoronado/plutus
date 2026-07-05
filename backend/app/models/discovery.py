from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

SCAN_STATUSES = ("queued", "running", "done", "failed")
CANDIDATE_STATUSES = ("new", "reviewed", "starred", "dismissed")
NOTIFY_MODES = ("off", "instant", "digest")
NOTIFICATION_CHANNELS = ("email", "telegram")
NOTIFICATION_KINDS = ("instant", "digest", "test", "maturity")


class Mandate(Base):
    """A standing scan instruction: universe + rules + schedule + signal weights."""

    __tablename__ = "mandates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    asset_class: Mapped[str] = mapped_column(Text)
    # discriminated on "type": class | watchlist | market_cap_floor | top_by_market_cap
    universe_def: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # screener AST applied as the coarse filter; NULL = universe-only mandate
    rules: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # 5-field cron string evaluated in the app timezone
    schedule: Mapped[str] = mapped_column(Text)
    # {"signal_key": weight >= 0}; at least one weight > 0
    score_weights: Mapped[dict[str, Any]] = mapped_column(JSONB)
    min_score: Mapped[float] = mapped_column(Float, default=40.0, server_default="40")
    # alert threshold; NULL means "same as min_score"
    notify_min_score: Mapped[float | None] = mapped_column(Float)
    max_candidates: Mapped[int] = mapped_column(Integer, default=20, server_default="20")
    cooldown_days: Mapped[int] = mapped_column(Integer, default=7, server_default="7")
    notify: Mapped[str] = mapped_column(Text, default="instant", server_default="instant")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "asset_class IN ('stock','etf','crypto','forex')", name="ck_mandates_asset_class"
        ),
        CheckConstraint("min_score >= 0 AND min_score <= 100", name="ck_mandates_min_score"),
        CheckConstraint(
            "notify_min_score IS NULL OR (notify_min_score >= 0 AND notify_min_score <= 100)",
            name="ck_mandates_notify_min_score",
        ),
        CheckConstraint(
            "max_candidates >= 1 AND max_candidates <= 100", name="ck_mandates_max_candidates"
        ),
        CheckConstraint(
            "cooldown_days >= 0 AND cooldown_days <= 90", name="ck_mandates_cooldown_days"
        ),
        CheckConstraint("notify IN ('off','instant','digest')", name="ck_mandates_notify"),
    )


class Scan(Base):
    """One execution of a mandate, run by the worker and polled by the UI."""

    __tablename__ = "scans"

    id: Mapped[int] = mapped_column(primary_key=True)
    mandate_id: Mapped[int] = mapped_column(ForeignKey("mandates.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(Text, default="queued", server_default="queued")
    # {universe, after_rules, analyzed, created, skipped_recent, skipped_no_data,
    #  as_of, duration_ms}
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','done','failed')", name="ck_scans_status"
        ),
        Index("ix_scans_mandate_created", "mandate_id", "created_at"),
    )


class Candidate(Base):
    """A scored opportunity produced by a scan; lives in the Research Inbox."""

    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    mandate_id: Mapped[int] = mapped_column(ForeignKey("mandates.id", ondelete="CASCADE"))
    scan_id: Mapped[int | None] = mapped_column(ForeignKey("scans.id", ondelete="SET NULL"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    # as-of timestamp of the bar data that triggered the candidate
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    score: Mapped[float] = mapped_column(Float)
    # ordered [{key, label, score, weight, triggered, evidence}, ...] — card-ready
    signals: Mapped[list[Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, default="new", server_default="new")
    # {snapshot, history_check, chart} — evidence generated at scan time
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "status IN ('new','reviewed','starred','dismissed')", name="ck_candidates_status"
        ),
        Index("ix_candidates_status_score", "status", "score"),
        Index("ix_candidates_mandate_asset_created", "mandate_id", "asset_id", "created_at"),
        Index("ix_candidates_created_at", "created_at"),
    )


class Notification(Base):
    """Audit log of alert send attempts (unconfigured channels never insert)."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    # {mandate_id, scan_id, candidate_ids}
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    ok: Mapped[bool] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("channel IN ('email','telegram')", name="ck_notifications_channel"),
        CheckConstraint(
            "kind IN ('instant','digest','test','maturity')", name="ck_notifications_kind"
        ),
        Index("ix_notifications_sent_at", "sent_at"),
    )
