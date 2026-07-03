"""assets, ohlcv hypertable, ingestion_runs

Revision ID: 0001
Revises:
Create Date: 2026-07-03

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("asset_class", sa.Text(), nullable=False),
        sa.Column("exchange", sa.Text(), nullable=True),
        sa.Column("currency", sa.Text(), nullable=False, server_default="USD"),
        sa.Column("metadata", JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("symbol", "asset_class", name="uq_assets_symbol_asset_class"),
        sa.CheckConstraint(
            "asset_class IN ('stock','etf','crypto','forex')",
            name="ck_assets_asset_class_valid",
        ),
    )
    op.create_index("ix_assets_asset_class", "assets", ["asset_class"])

    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("job_name", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("asset_class", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.Column("rows_written", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("symbols_ok", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("symbols_failed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("details", JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.CheckConstraint(
            "status IN ('running','success','partial','failed')",
            name="ck_ingestion_runs_status_valid",
        ),
    )
    op.create_index(
        "ix_ingestion_runs_started_at",
        "ingestion_runs",
        [sa.text("started_at DESC")],
    )

    op.create_table(
        "ohlcv",
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("interval", sa.Text(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Double(), nullable=False),
        sa.Column("high", sa.Double(), nullable=False),
        sa.Column("low", sa.Double(), nullable=False),
        sa.Column("close", sa.Double(), nullable=False),
        sa.Column("volume", sa.Double(), nullable=True),
        sa.PrimaryKeyConstraint("asset_id", "interval", "ts", name="pk_ohlcv"),
        sa.CheckConstraint(
            "interval IN ('1m','5m','15m','1h','4h','1d','1w')",
            name="ck_ohlcv_interval_valid",
        ),
    )

    # Daily data for a personal universe: 30-day chunks keep chunk counts sane
    op.execute("SELECT create_hypertable('ohlcv', by_range('ts', INTERVAL '30 days'))")
    op.execute(
        """
        ALTER TABLE ohlcv SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'asset_id, "interval"',
            timescaledb.compress_orderby = 'ts DESC'
        )
        """
    )
    # Spec §4.2: compression policy after 30 days
    op.execute("SELECT add_compression_policy('ohlcv', INTERVAL '30 days')")


def downgrade() -> None:
    op.execute("SELECT remove_compression_policy('ohlcv', if_exists => true)")
    op.drop_table("ohlcv")
    op.drop_index("ix_ingestion_runs_started_at", table_name="ingestion_runs")
    op.drop_table("ingestion_runs")
    op.drop_index("ix_assets_asset_class", table_name="assets")
    op.drop_table("assets")
