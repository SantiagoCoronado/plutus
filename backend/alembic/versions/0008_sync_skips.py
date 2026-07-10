"""Phase 8 M1: exchange_sync_skips — items the sync could not land yet

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-10

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "exchange_sync_skips",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stream", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        # normalized provider row, enough to build the transaction once resolvable
        sa.Column("payload", JSONB, nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "stream IN ('trade','funding','withdrawal')",
            name="ck_exchange_sync_skips_stream",
        ),
        sa.CheckConstraint(
            "reason IN ('unknown_symbol','pending_status')",
            name="ck_exchange_sync_skips_reason",
        ),
        sa.UniqueConstraint(
            "account_id", "stream", "external_id", name="uq_exchange_sync_skips_item"
        ),
    )
    op.create_index(
        "ix_exchange_sync_skips_unresolved",
        "exchange_sync_skips",
        ["account_id"],
        postgresql_where=sa.text("resolved_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_exchange_sync_skips_unresolved", table_name="exchange_sync_skips")
    op.drop_table("exchange_sync_skips")
