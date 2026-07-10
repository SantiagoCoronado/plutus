"""Phase 8 M4: append-only bank-investment term history

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-10

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bank_investment_terms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "investment_id",
            sa.Integer(),
            sa.ForeignKey("bank_investments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("start_date", sa.Date(), nullable=False),
        # NULL marks the currently-active term
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("principal", sa.Numeric(20, 8), nullable=False),
        sa.Column("annual_rate", sa.Numeric(10, 6), nullable=False),
        sa.Column("rate_tiers", JSONB(), nullable=True),
        sa.Column("cap_amount", sa.Numeric(20, 8), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("principal > 0", name="ck_bank_investment_terms_principal"),
        sa.CheckConstraint("annual_rate >= 0", name="ck_bank_investment_terms_annual_rate"),
        sa.CheckConstraint(
            "end_date IS NULL OR end_date > start_date", name="ck_bank_investment_terms_dates"
        ),
    )
    op.create_index(
        "ix_bank_investment_terms_investment_start",
        "bank_investment_terms",
        ["investment_id", "start_date"],
    )
    # no backfill on purpose: an investment without history rows reads as the
    # single term its parent row describes, so existing data keeps working


def downgrade() -> None:
    op.drop_index(
        "ix_bank_investment_terms_investment_start", table_name="bank_investment_terms"
    )
    op.drop_table("bank_investment_terms")
