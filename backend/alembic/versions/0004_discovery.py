"""discovery engine: mandates, scans, candidates, notifications

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-04

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mandates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("asset_class", sa.Text(), nullable=False),
        sa.Column("universe_def", JSONB(), nullable=False),
        sa.Column("rules", JSONB(), nullable=True),
        sa.Column("schedule", sa.Text(), nullable=False),
        sa.Column("score_weights", JSONB(), nullable=False),
        sa.Column("min_score", sa.Float(), nullable=False, server_default="40"),
        sa.Column("notify_min_score", sa.Float(), nullable=True),
        sa.Column("max_candidates", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("cooldown_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("notify", sa.Text(), nullable=False, server_default="instant"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("name", name="uq_mandates_name"),
        sa.CheckConstraint(
            "asset_class IN ('stock','etf','crypto','forex')", name="ck_mandates_asset_class"
        ),
        sa.CheckConstraint("min_score >= 0 AND min_score <= 100", name="ck_mandates_min_score"),
        sa.CheckConstraint(
            "notify_min_score IS NULL OR (notify_min_score >= 0 AND notify_min_score <= 100)",
            name="ck_mandates_notify_min_score",
        ),
        sa.CheckConstraint(
            "max_candidates >= 1 AND max_candidates <= 100", name="ck_mandates_max_candidates"
        ),
        sa.CheckConstraint(
            "cooldown_days >= 0 AND cooldown_days <= 90", name="ck_mandates_cooldown_days"
        ),
        sa.CheckConstraint("notify IN ('off','instant','digest')", name="ck_mandates_notify"),
    )

    op.create_table(
        "scans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "mandate_id",
            sa.Integer(),
            sa.ForeignKey("mandates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("stats", JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','done','failed')", name="ck_scans_status"
        ),
    )
    op.create_index("ix_scans_mandate_created", "scans", ["mandate_id", "created_at"])

    op.create_table(
        "candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "mandate_id",
            sa.Integer(),
            sa.ForeignKey("mandates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "scan_id",
            sa.Integer(),
            sa.ForeignKey("scans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("signals", JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="new"),
        sa.Column("context", JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('new','reviewed','starred','dismissed')", name="ck_candidates_status"
        ),
    )
    op.create_index("ix_candidates_status_score", "candidates", ["status", "score"])
    op.create_index(
        "ix_candidates_mandate_asset_created",
        "candidates",
        ["mandate_id", "asset_id", "created_at"],
    )
    op.create_index("ix_candidates_created_at", "candidates", ["created_at"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("meta", JSONB(), nullable=False, server_default="{}"),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("channel IN ('email','telegram')", name="ck_notifications_channel"),
        sa.CheckConstraint("kind IN ('instant','digest','test')", name="ck_notifications_kind"),
    )
    op.create_index("ix_notifications_sent_at", "notifications", ["sent_at"])


def downgrade() -> None:
    op.drop_index("ix_notifications_sent_at", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_candidates_created_at", table_name="candidates")
    op.drop_index("ix_candidates_mandate_asset_created", table_name="candidates")
    op.drop_index("ix_candidates_status_score", table_name="candidates")
    op.drop_table("candidates")
    op.drop_index("ix_scans_mandate_created", table_name="scans")
    op.drop_table("scans")
    op.drop_table("mandates")
