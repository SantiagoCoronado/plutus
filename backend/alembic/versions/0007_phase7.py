"""Phase 7 integrations: price alert rules + exchange sync bookkeeping

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-06

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("condition", sa.Text(), nullable=False),
        sa.Column("threshold", sa.Numeric(20, 8), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="armed"),
        # reserved for a future repeating mode; unused in the one-shot UI
        sa.Column("cooldown_minutes", sa.Integer(), nullable=True),
        # crossing-edge memory: which side of the threshold the last quote sat on
        sa.Column("last_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
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
        sa.CheckConstraint("condition IN ('above','below')", name="ck_alert_rules_condition"),
        sa.CheckConstraint("threshold > 0", name="ck_alert_rules_threshold"),
        sa.CheckConstraint(
            "status IN ('armed','triggered','disabled')", name="ck_alert_rules_status"
        ),
    )
    op.create_index("ix_alert_rules_status_asset", "alert_rules", ["status", "asset_id"])

    op.create_table(
        "exchange_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        # resume cursors — keys live encrypted in app_settings, never here
        sa.Column("last_trade_tid", sa.Text(), nullable=True),
        sa.Column("last_funding_id", sa.Text(), nullable=True),
        sa.Column("last_withdrawal_id", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("provider IN ('bitso')", name="ck_exchange_links_provider"),
    )

    op.create_table(
        "exchange_sync_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("trades_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trades_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("details", JSONB(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running','success','partial','failed')",
            name="ck_exchange_sync_runs_status",
        ),
    )
    op.create_index(
        "ix_exchange_sync_runs_account_started",
        "exchange_sync_runs",
        ["account_id", sa.text("started_at DESC")],
    )

    # per-asset price alerts deliver through the shared notification audit table
    op.drop_constraint("ck_notifications_kind", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notifications_kind",
        "notifications",
        "kind IN ('instant','digest','test','maturity','memo','price_alert')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_notifications_kind", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notifications_kind",
        "notifications",
        "kind IN ('instant','digest','test','maturity','memo')",
    )
    op.drop_index("ix_exchange_sync_runs_account_started", table_name="exchange_sync_runs")
    op.drop_table("exchange_sync_runs")
    op.drop_table("exchange_links")
    op.drop_index("ix_alert_rules_status_asset", table_name="alert_rules")
    op.drop_table("alert_rules")
