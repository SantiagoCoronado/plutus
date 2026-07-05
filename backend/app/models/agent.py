from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

CONVERSATION_KINDS = ("chat", "task", "translate")
CONVERSATION_STATUSES = ("active", "queued", "running", "done", "error")
MESSAGE_ROLES = ("user", "assistant", "tool", "system")
TOOL_CALL_SOURCES = ("app", "task", "mcp")
TOOL_TIERS = ("read", "write")
TOOL_CALL_STATUSES = (
    "done",
    "error",
    "pending_confirmation",
    "approved",
    "rejected",
    "expired",
)


class AppSetting(Base):
    """Key-value app configuration; secret values are Fernet-encrypted at rest.

    A row here overrides the same-named env var, so the LLM provider can be
    switched from the Settings UI without a restart.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentConversation(Base):
    """One agent interaction thread: a chat, a research task run, or a translation.

    Research tasks and translations get a conversation too so token usage and
    transcripts live in one place; their lifecycle rides `status` (runner
    pattern — failures become row state, not worker errors).
    """

    __tablename__ = "agent_conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(Text, default="chat", server_default="chat")
    title: Mapped[str | None] = mapped_column(Text)
    # autonomous = write-tier tools execute without a confirmation card
    autonomous: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    status: Mapped[str] = mapped_column(Text, default="active", server_default="active")
    # last provider/model that produced a message in this conversation
    provider: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    # claude-agent-sdk session id for resume (claude-subscription provider only)
    sidecar_session_id: Mapped[str | None] = mapped_column(Text)
    # {asset_id, candidate_id, trigger} for kind='task'
    task_meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('chat','task','translate')", name="ck_agent_conversations_kind"
        ),
        CheckConstraint(
            "status IN ('active','queued','running','done','error')",
            name="ck_agent_conversations_status",
        ),
        Index("ix_agent_conversations_kind_created", "kind", "created_at"),
    )


class AgentMessage(Base):
    """One turn element in a conversation, in provider-neutral shape."""

    __tablename__ = "agent_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    # assistant: [{"id", "name", "arguments"}]
    tool_calls: Mapped[list[Any] | None] = mapped_column(JSONB)
    # role='tool' rows: which call this result answers
    tool_call_id: Mapped[str | None] = mapped_column(Text)
    tool_name: Mapped[str | None] = mapped_column(Text)
    tool_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    provider: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "role IN ('user','assistant','tool','system')", name="ck_agent_messages_role"
        ),
        Index("ix_agent_messages_conversation", "conversation_id", "id"),
        # the daily token-budget SUM scans by day
        Index("ix_agent_messages_created_at", "created_at"),
    )


class AgentToolCall(Base):
    """Audit log of every agent tool invocation (spec §13.4).

    The row id doubles as the confirmation id for write-tier calls awaiting
    approval; the Settings "recent agent actions" feed reads this table.
    """

    __tablename__ = "agent_tool_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(Text)
    tier: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(Text)
    result_summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("source IN ('app','task','mcp')", name="ck_agent_tool_calls_source"),
        CheckConstraint("tier IN ('read','write')", name="ck_agent_tool_calls_tier"),
        CheckConstraint(
            "status IN ('done','error','pending_confirmation','approved','rejected','expired')",
            name="ck_agent_tool_calls_status",
        ),
        Index("ix_agent_tool_calls_created", "created_at"),
        Index(
            "ix_agent_tool_calls_pending",
            "status",
            postgresql_where=text("status = 'pending_confirmation'"),
        ),
    )
