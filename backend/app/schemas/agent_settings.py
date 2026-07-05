from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

LLMProviderName = Literal[
    "claude-subscription", "anthropic-api", "openai", "google", "openrouter", "ollama"
]


class SidecarStatusOut(BaseModel):
    url: str
    reachable: bool
    auth_ok: bool


class LLMSettingsOut(BaseModel):
    provider: str
    model: str
    # masked key previews ("sk-ant…f3ab"), null when unset
    keys: dict[str, str | None]
    sidecar: SidecarStatusOut
    daily_token_budget: int
    fernet_configured: bool


class LLMSettingsIn(BaseModel):
    provider: LLMProviderName | None = None
    model: str | None = Field(default=None, max_length=200)
    # write-only: only keys present here are changed; empty string clears a key
    keys: dict[str, str] | None = None


class TestConnectionIn(BaseModel):
    provider: LLMProviderName | None = None  # default: the configured provider


class TestConnectionOut(BaseModel):
    ok: bool
    provider: str
    detail: str


class UsageOut(BaseModel):
    date: str
    tokens_used: int
    daily_token_budget: int
    remaining: int


class AgentActionOut(BaseModel):
    id: int
    conversation_id: int | None
    source: str
    tier: str
    name: str
    arguments: dict
    status: str
    result_summary: str | None
    error: str | None
    created_at: datetime
