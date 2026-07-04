"""screener + backtesting: screens, backtests

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-03

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "screens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("asset_class", sa.Text(), nullable=True),
        sa.Column("ast", JSONB(), nullable=False),
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
        sa.UniqueConstraint("name", name="uq_screens_name"),
        sa.CheckConstraint(
            "asset_class IN ('stock','etf','crypto','forex')", name="ck_screens_asset_class"
        ),
    )

    op.create_table(
        "backtests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column(
            "screen_id",
            sa.Integer(),
            sa.ForeignKey("screens.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("params", JSONB(), nullable=False),
        sa.Column("stats", JSONB(), nullable=True),
        sa.Column("equity_curve", JSONB(), nullable=True),
        sa.Column("trade_list", JSONB(), nullable=True),
        sa.Column("artifact_path", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('screen','strategy')", name="ck_backtests_kind"),
        sa.CheckConstraint(
            "status IN ('queued','running','done','failed')", name="ck_backtests_status"
        ),
    )
    op.create_index("ix_backtests_created_at", "backtests", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_backtests_created_at", table_name="backtests")
    op.drop_table("backtests")
    op.drop_table("screens")
