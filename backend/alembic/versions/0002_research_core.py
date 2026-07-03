"""research core: asset_metrics, fundamentals, watchlists, notes, news

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-03

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from alembic import op
from app.models.asset_metrics import METRIC_COLUMNS
from app.models.fundamentals import FUNDAMENTAL_COLUMNS

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asset_metrics",
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("benchmark_symbol", sa.Text(), nullable=True),
        sa.Column("extras", JSONB(), nullable=False, server_default=sa.text("'{}'")),
        *[sa.Column(name, sa.Double(), nullable=True) for name in METRIC_COLUMNS],
    )

    op.create_table(
        "fundamentals",
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=True),
        sa.Column("currency", sa.Text(), nullable=False, server_default="USD"),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        *[sa.Column(name, sa.Double(), nullable=True) for name in FUNDAMENTAL_COLUMNS],
        sa.Column("metrics", JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.PrimaryKeyConstraint("asset_id", "period", "report_date", name="pk_fundamentals"),
        sa.CheckConstraint(
            "period IN ('annual','quarter','ttm')", name="ck_fundamentals_period_valid"
        ),
    )

    op.create_table(
        "watchlists",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("name", name="uq_watchlists_name"),
    )
    op.create_table(
        "watchlist_items",
        sa.Column(
            "watchlist_id",
            sa.Integer(),
            sa.ForeignKey("watchlists.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # the research page's star button needs a target with zero setup
    op.execute("INSERT INTO watchlists (name) VALUES ('Default')")

    op.create_table(
        "asset_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("body_md", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default="user"),
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
        sa.CheckConstraint("source IN ('user','ai')", name="ck_asset_notes_source_valid"),
    )
    op.create_index("ix_asset_notes_asset_id", "asset_notes", ["asset_id"])

    op.create_table(
        "news_items",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("tickers", ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("sentiment", sa.Double(), nullable=True),
    )
    # md5 expression: dedup target for upserts, immune to b-tree limits on long URLs
    op.create_index(
        "uq_news_items_url_md5", "news_items", [sa.text("md5(url)")], unique=True
    )
    op.create_index("ix_news_items_ts", "news_items", [sa.text("ts DESC")])
    op.create_index(
        "ix_news_items_tickers", "news_items", ["tickers"], postgresql_using="gin"
    )


def downgrade() -> None:
    op.drop_table("news_items")
    op.drop_table("asset_notes")
    op.drop_table("watchlist_items")
    op.drop_table("watchlists")
    op.drop_table("fundamentals")
    op.drop_table("asset_metrics")
