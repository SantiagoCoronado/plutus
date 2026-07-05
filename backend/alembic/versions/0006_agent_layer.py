"""AI agent layer: app_settings, conversations, messages, tool-call audit, translations

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-05

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("is_secret", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "agent_conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False, server_default="chat"),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("autonomous", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("sidecar_session_id", sa.Text(), nullable=True),
        sa.Column("task_meta", JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "kind IN ('chat','task','translate')", name="ck_agent_conversations_kind"
        ),
        sa.CheckConstraint(
            "status IN ('active','queued','running','done','error')",
            name="ck_agent_conversations_status",
        ),
    )
    op.create_index(
        "ix_agent_conversations_kind_created",
        "agent_conversations",
        ["kind", "created_at"],
    )

    op.create_table(
        "agent_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("agent_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_calls", JSONB(), nullable=True),
        sa.Column("tool_call_id", sa.Text(), nullable=True),
        sa.Column("tool_name", sa.Text(), nullable=True),
        sa.Column("tool_result", JSONB(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "role IN ('user','assistant','tool','system')", name="ck_agent_messages_role"
        ),
    )
    op.create_index(
        "ix_agent_messages_conversation", "agent_messages", ["conversation_id", "id"]
    )
    op.create_index("ix_agent_messages_created_at", "agent_messages", ["created_at"])

    op.create_table(
        "agent_tool_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("agent_conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("arguments", JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("source IN ('app','task','mcp')", name="ck_agent_tool_calls_source"),
        sa.CheckConstraint("tier IN ('read','write')", name="ck_agent_tool_calls_tier"),
        sa.CheckConstraint(
            "status IN ('done','error','pending_confirmation','approved','rejected','expired')",
            name="ck_agent_tool_calls_status",
        ),
    )
    op.create_index("ix_agent_tool_calls_created", "agent_tool_calls", ["created_at"])
    op.create_index(
        "ix_agent_tool_calls_pending",
        "agent_tool_calls",
        ["status"],
        postgresql_where=sa.text("status = 'pending_confirmation'"),
    )

    op.create_table(
        "strategy_translations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("agent_conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_content", sa.Text(), nullable=False),
        sa.Column("understanding_md", sa.Text(), nullable=True),
        sa.Column("limitations", JSONB(), nullable=True),
        sa.Column("spec", JSONB(), nullable=True),
        sa.Column("symbol", sa.Text(), nullable=True),
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("translatable", sa.Boolean(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column(
            "backtest_id",
            sa.Integer(),
            sa.ForeignKey("backtests.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('draft','confirmed','discarded','failed')",
            name="ck_strategy_translations_status",
        ),
    )
    op.create_index(
        "ix_strategy_translations_created", "strategy_translations", ["created_at"]
    )

    # research-memo alerts reuse the alert audit table
    op.drop_constraint("ck_notifications_kind", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notifications_kind",
        "notifications",
        "kind IN ('instant','digest','test','maturity','memo')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_notifications_kind", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notifications_kind",
        "notifications",
        "kind IN ('instant','digest','test','maturity')",
    )
    op.drop_index("ix_strategy_translations_created", table_name="strategy_translations")
    op.drop_table("strategy_translations")
    op.drop_index("ix_agent_tool_calls_pending", table_name="agent_tool_calls")
    op.drop_index("ix_agent_tool_calls_created", table_name="agent_tool_calls")
    op.drop_table("agent_tool_calls")
    op.drop_index("ix_agent_messages_created_at", table_name="agent_messages")
    op.drop_index("ix_agent_messages_conversation", table_name="agent_messages")
    op.drop_table("agent_messages")
    op.drop_index("ix_agent_conversations_kind_created", table_name="agent_conversations")
    op.drop_table("agent_conversations")
    op.drop_table("app_settings")
