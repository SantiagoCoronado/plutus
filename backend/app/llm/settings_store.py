"""LLM settings resolution: app_settings rows (Settings UI) override env vars.

Editable keys are a closed set — provider, model, and the per-provider API
keys. Infrastructure URLs (sidecar, ollama) stay env-only. Secret values are
Fernet-encrypted at rest and only ever returned masked; the raw value is
decrypted just-in-time by `get_llm_settings` for provider construction.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.llm.crypto import SecretDecryptError, decrypt_text, encrypt_text

LLM_PROVIDERS = (
    "claude-subscription",
    "anthropic-api",
    "openai",
    "google",
    "openrouter",
    "ollama",
)

# key -> is_secret; the only keys PUT /agent/settings accepts
EDITABLE_KEYS: dict[str, bool] = {
    "llm_provider": False,
    "llm_model": False,
    "anthropic_api_key": True,
    "openai_api_key": True,
    "google_api_key": True,
    "openrouter_api_key": True,
}

# which stored key each provider authenticates with (None = no key needed)
PROVIDER_KEY_FIELD: dict[str, str | None] = {
    "claude-subscription": None,
    "anthropic-api": "anthropic_api_key",
    "openai": "openai_api_key",
    "google": "google_api_key",
    "openrouter": "openrouter_api_key",
    "ollama": None,
}


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    model: str
    anthropic_api_key: str
    openai_api_key: str
    google_api_key: str
    openrouter_api_key: str
    claude_sidecar_url: str
    ollama_base_url: str

    def api_key_for(self, provider: str | None = None) -> str:
        field = PROVIDER_KEY_FIELD.get(provider or self.provider)
        return getattr(self, field) if field else ""


def _stored_values(session: Session) -> dict[str, str]:
    from app.models import AppSetting

    values: dict[str, str] = {}
    for row in session.scalars(select(AppSetting)).all():
        if row.is_secret:
            try:
                values[row.key] = decrypt_text(row.value)
            except SecretDecryptError:
                # unusable ciphertext degrades to "not configured", never a 500
                continue
        else:
            values[row.key] = row.value
    return values


def get_llm_settings(session: Session) -> LLMSettings:
    env = get_settings()
    stored = _stored_values(session)

    def pick(key: str, env_value: str) -> str:
        return stored.get(key, env_value)

    provider = pick("llm_provider", env.llm_provider)
    if provider not in LLM_PROVIDERS:
        provider = "claude-subscription"
    return LLMSettings(
        provider=provider,
        model=pick("llm_model", env.llm_model),
        anthropic_api_key=pick("anthropic_api_key", env.anthropic_api_key),
        openai_api_key=pick("openai_api_key", env.openai_api_key),
        google_api_key=pick("google_api_key", env.google_api_key),
        openrouter_api_key=pick("openrouter_api_key", env.openrouter_api_key),
        claude_sidecar_url=env.claude_sidecar_url,
        ollama_base_url=env.ollama_base_url,
    )


def set_setting(session: Session, key: str, value: str) -> None:
    """Upsert one editable setting; secrets are encrypted before they touch the DB."""
    from app.models import AppSetting

    if key not in EDITABLE_KEYS:
        raise KeyError(key)
    is_secret = EDITABLE_KEYS[key]
    stored_value = encrypt_text(value) if is_secret and value else value
    row = session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=stored_value, is_secret=is_secret))
    else:
        row.value = stored_value
        row.is_secret = is_secret


def mask_secret(value: str) -> str | None:
    """'sk-ant-…f3ab' — enough to recognize a key without exposing it."""
    if not value:
        return None
    if len(value) <= 8:
        return "…" + value[-2:]
    return value[:6] + "…" + value[-4:]
