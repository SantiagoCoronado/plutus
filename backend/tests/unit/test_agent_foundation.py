"""Unit tests for the agent-layer foundation: secret crypto, settings masking,
and the token-budget day boundary."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet

from app.core.config import get_settings
from app.llm.budget import _day_start_utc
from app.llm.crypto import FernetKeyMissing, SecretDecryptError, decrypt_text, encrypt_text
from app.llm.settings_store import EDITABLE_KEYS, LLM_PROVIDERS, mask_secret


@pytest.fixture
def fernet_env(monkeypatch):
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestCrypto:
    def test_roundtrip(self, fernet_env):
        secret = "sk-ant-api03-abc123"
        ciphertext = encrypt_text(secret)
        assert ciphertext != secret
        assert secret not in ciphertext
        assert decrypt_text(ciphertext) == secret

    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.setenv("FERNET_KEY", "")
        get_settings.cache_clear()
        with pytest.raises(FernetKeyMissing):
            encrypt_text("anything")
        get_settings.cache_clear()

    def test_wrong_key_decrypt_fails(self, monkeypatch):
        monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
        get_settings.cache_clear()
        ciphertext = encrypt_text("secret")
        monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
        get_settings.cache_clear()
        with pytest.raises(SecretDecryptError):
            decrypt_text(ciphertext)
        get_settings.cache_clear()


class TestMasking:
    def test_masks_middle(self):
        assert mask_secret("sk-ant-api03-verylongkey-f3ab") == "sk-ant…f3ab"

    def test_short_values_barely_shown(self):
        assert mask_secret("abcdefgh") == "…gh"

    def test_empty_is_none(self):
        assert mask_secret("") is None

    def test_masked_never_contains_bulk_of_key(self):
        key = "sk-" + "x" * 60 + "tail"
        masked = mask_secret(key)
        assert len(masked) < 15
        assert "x" * 10 not in masked


class TestProviderRegistry:
    def test_all_spec_providers_selectable(self):
        assert set(LLM_PROVIDERS) == {
            "claude-subscription",
            "anthropic-api",
            "openai",
            "google",
            "openrouter",
            "ollama",
        }

    def test_only_api_keys_are_secret(self):
        secret_keys = {k for k, is_secret in EDITABLE_KEYS.items() if is_secret}
        assert secret_keys == {
            "anthropic_api_key",
            "openai_api_key",
            "google_api_key",
            "openrouter_api_key",
        }


class TestBudgetDayBoundary:
    def test_day_starts_at_local_midnight(self, monkeypatch):
        monkeypatch.setenv("TZ", "America/Mexico_City")
        get_settings.cache_clear()
        start = _day_start_utc()
        local = start.astimezone(ZoneInfo("America/Mexico_City"))
        assert (local.hour, local.minute, local.second) == (0, 0, 0)
        assert local.date() == datetime.now(ZoneInfo("America/Mexico_City")).date()
        get_settings.cache_clear()
