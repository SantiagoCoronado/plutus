from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConversationIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    autonomous: bool | None = None


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    title: str | None
    autonomous: bool
    status: str
    provider: str | None
    model: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str | None
    tool_calls: list[dict[str, Any]] | None
    tool_call_id: str | None
    tool_name: str | None
    tool_result: dict[str, Any] | None
    provider: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    created_at: datetime


class ConfirmationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    arguments: dict[str, Any]
    result_summary: str | None
    status: str
    created_at: datetime


class ConversationDetailOut(BaseModel):
    conversation: ConversationOut
    messages: list[MessageOut]
    pending_confirmations: list[ConfirmationOut]


class SendMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=32_000)


class ConfirmationResolutionOut(BaseModel):
    ok: bool
    status: str
    result_summary: str | None = None
    error: str | None = None
    result: Any = None


class ToolExecuteIn(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    conversation_id: int | None = None
