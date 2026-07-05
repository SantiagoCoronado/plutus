"""Provider factory: settings → concrete adapter.

Three implementations cover all six selectable providers — the OpenAI-compatible
adapter serves openai / openrouter / google / ollama with different base URLs.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.llm.base import AgentLoopProvider, ChatProvider, LLMError
from app.llm.settings_store import LLMSettings, get_llm_settings

# test seam: install a FakeProvider here and every surface uses it
_PROVIDER_OVERRIDE: ChatProvider | AgentLoopProvider | None = None


def set_provider_override(provider: ChatProvider | AgentLoopProvider | None) -> None:
    global _PROVIDER_OVERRIDE
    _PROVIDER_OVERRIDE = provider


def get_provider(session: Session) -> ChatProvider | AgentLoopProvider:
    if _PROVIDER_OVERRIDE is not None:
        return _PROVIDER_OVERRIDE
    return build_provider(get_llm_settings(session))


def build_provider(cfg: LLMSettings) -> ChatProvider | AgentLoopProvider:
    if cfg.provider == "claude-subscription":
        from app.llm.providers.claude_sidecar import ClaudeSidecarProvider

        return ClaudeSidecarProvider(base_url=cfg.claude_sidecar_url, model=cfg.model)
    if cfg.provider == "anthropic-api":
        from app.llm.providers.anthropic_api import AnthropicProvider

        if not cfg.anthropic_api_key:
            raise LLMError("anthropic-api needs an API key — add it in Settings")
        return AnthropicProvider(api_key=cfg.anthropic_api_key, model=cfg.model)

    from app.llm.providers.openai_compat import OPENAI_COMPAT_PRESETS, OpenAICompatProvider

    preset = OPENAI_COMPAT_PRESETS.get(cfg.provider)
    if preset is None:
        raise LLMError(f"unknown provider '{cfg.provider}'")
    api_key = cfg.api_key_for(cfg.provider)
    if preset.requires_key and not api_key:
        raise LLMError(f"{cfg.provider} needs an API key — add it in Settings")
    if cfg.provider == "ollama":
        base_url = cfg.ollama_base_url.rstrip("/") + "/v1"
    else:
        base_url = preset.base_url
    return OpenAICompatProvider(
        provider_name=cfg.provider,
        base_url=base_url,
        api_key=api_key,
        model=cfg.model or preset.default_model,
    )
