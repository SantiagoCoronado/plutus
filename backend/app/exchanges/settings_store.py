"""Exchange credential storage — parallel to app/llm/settings_store.py but
exchange-scoped, so the LLM key set stays untouched.

Bitso API key + secret are Fernet-encrypted at rest in app_settings and only
ever surfaced masked. The raw values are decrypted just-in-time to build the
signed client. A closed EDITABLE_KEYS set is the only thing the route may write.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.llm.crypto import SecretDecryptError, decrypt_text, encrypt_text
from app.llm.settings_store import mask_secret

log = get_logger(__name__)

# key -> is_secret; both Bitso credentials are secret
EXCHANGE_EDITABLE_KEYS: dict[str, bool] = {
    "bitso_api_key": True,
    "bitso_api_secret": True,
}


def set_exchange_setting(session: Session, key: str, value: str) -> None:
    """Upsert one editable exchange setting; secrets are encrypted before the DB."""
    from app.models import AppSetting

    if key not in EXCHANGE_EDITABLE_KEYS:
        raise KeyError(key)
    is_secret = EXCHANGE_EDITABLE_KEYS[key]
    stored_value = encrypt_text(value) if is_secret and value else value
    row = session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=stored_value, is_secret=is_secret))
    else:
        row.value = stored_value
        row.is_secret = is_secret


def _read_secret(session: Session, key: str) -> str | None:
    from app.models import AppSetting

    row = session.get(AppSetting, key)
    if row is None or not row.value:
        return None
    if not row.is_secret:
        return row.value
    try:
        return decrypt_text(row.value)
    except SecretDecryptError:
        # unusable ciphertext degrades to "not configured", never a 500
        log.warning("bitso credential could not be decrypted", key=key)
        return None


def get_bitso_credentials(session: Session) -> tuple[str, str] | None:
    """(api_key, api_secret) or None when either is missing/undecryptable."""
    api_key = _read_secret(session, "bitso_api_key")
    api_secret = _read_secret(session, "bitso_api_secret")
    if not api_key or not api_secret:
        return None
    return api_key, api_secret


def masked_bitso_keys(session: Session) -> dict[str, str | None]:
    """Masked view for GET /exchanges/status — never the raw values."""
    return {
        "bitso_api_key": mask_secret(_read_secret(session, "bitso_api_key") or ""),
        "bitso_api_secret": mask_secret(_read_secret(session, "bitso_api_secret") or ""),
    }
