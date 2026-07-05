"""portfolio tracking: accounts, transactions, bank_investments

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-05

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("currency", sa.Text(), nullable=False, server_default="USD"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("name", name="uq_accounts_name"),
        sa.CheckConstraint(
            "type IN ('brokerage','exchange','wallet','bank','manual')",
            name="ck_accounts_type",
        ),
    )

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        # asset units for buy/sell/transfer_*; cash amount for the money-only types
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        # per-unit price in `currency`; NULL for money-only types
        sa.Column("price", sa.Numeric(20, 8), nullable=True),
        sa.Column("fees", sa.Numeric(20, 8), nullable=False, server_default="0"),
        # currency of the cash leg (a Bitso stock buy is MXN against a USD-quoted asset)
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        # dedup key for CSV imports
        sa.Column("external_id", sa.Text(), nullable=True),
        # specific-ID sells: [{"buy_transaction_id": int, "quantity": num}]
        sa.Column("lot_links", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "type IN ('buy','sell','deposit','withdrawal','dividend','interest',"
            "'fee','transfer_in','transfer_out')",
            name="ck_transactions_type",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_transactions_quantity"),
        sa.CheckConstraint("price IS NULL OR price >= 0", name="ck_transactions_price"),
        sa.CheckConstraint("fees >= 0", name="ck_transactions_fees"),
        sa.CheckConstraint(
            "type NOT IN ('buy','sell','transfer_in','transfer_out','dividend')"
            " OR asset_id IS NOT NULL",
            name="ck_transactions_asset_required",
        ),
    )
    op.create_index("ix_transactions_account_ts", "transactions", ["account_id", "ts"])
    op.create_index("ix_transactions_asset_ts", "transactions", ["asset_id", "ts"])
    op.create_index(
        "uq_transactions_account_external",
        "transactions",
        ["account_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )

    op.create_table(
        "bank_investments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("principal", sa.Numeric(20, 8), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False, server_default="MXN"),
        # decimal fraction: 0.105 means 10.5% per year
        sa.Column("annual_rate", sa.Numeric(10, 6), nullable=False),
        # [{"up_to": 25000, "annual_rate": 0.15}, {"up_to": null, "annual_rate": 0.05}]
        sa.Column("rate_tiers", JSONB(), nullable=True),
        sa.Column("day_count", sa.Text(), nullable=False, server_default="act360"),
        sa.Column("compounding", sa.Text(), nullable=False, server_default="at_maturity"),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("term_days", sa.Integer(), nullable=True),
        # derived start_date + term_days; stored so maturity queries stay indexable
        sa.Column("maturity_date", sa.Date(), nullable=True),
        sa.Column("cap_amount", sa.Numeric(20, 8), nullable=True),
        sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
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
        sa.CheckConstraint("kind IN ('demand','fixed_term')", name="ck_bank_investments_kind"),
        sa.CheckConstraint("principal > 0", name="ck_bank_investments_principal"),
        sa.CheckConstraint("annual_rate >= 0", name="ck_bank_investments_annual_rate"),
        sa.CheckConstraint(
            "day_count IN ('act360','act365')", name="ck_bank_investments_day_count"
        ),
        sa.CheckConstraint(
            "compounding IN ('daily','monthly','at_maturity')",
            name="ck_bank_investments_compounding",
        ),
        sa.CheckConstraint(
            "term_days IS NULL OR term_days > 0", name="ck_bank_investments_term_days"
        ),
        sa.CheckConstraint(
            "kind = 'demand' OR term_days IS NOT NULL",
            name="ck_bank_investments_term_required",
        ),
        sa.CheckConstraint(
            "status IN ('active','matured','closed')", name="ck_bank_investments_status"
        ),
    )
    op.create_index(
        "ix_bank_investments_status_maturity",
        "bank_investments",
        ["status", "maturity_date"],
    )

    # maturity reminders reuse the alert audit table
    op.drop_constraint("ck_notifications_kind", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notifications_kind",
        "notifications",
        "kind IN ('instant','digest','test','maturity')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_notifications_kind", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notifications_kind",
        "notifications",
        "kind IN ('instant','digest','test')",
    )
    op.drop_index("ix_bank_investments_status_maturity", table_name="bank_investments")
    op.drop_table("bank_investments")
    op.drop_index("uq_transactions_account_external", table_name="transactions")
    op.drop_index("ix_transactions_asset_ts", table_name="transactions")
    op.drop_index("ix_transactions_account_ts", table_name="transactions")
    op.drop_table("transactions")
    op.drop_table("accounts")
