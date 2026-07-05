"""Fernet encryption for secret app_settings values (LLM API keys).

The key comes from the FERNET_KEY env var (`Fernet.generate_key()` output).
Without it, storing a secret is refused with a clear error — plaintext keys
never reach the database.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class FernetKeyMissing(Exception):
    def __init__(self) -> None:
        super().__init__(
            "FERNET_KEY is not set — generate one with "
            '`python -c "from cryptography.fernet import Fernet; '
            "print(Fernet.generate_key().decode())\"` and add it to .env"
        )


class SecretDecryptError(Exception):
    """Stored ciphertext can't be decrypted (key changed or value corrupted)."""


def _fernet() -> Fernet:
    key = get_settings().fernet_key
    if not key:
        raise FernetKeyMissing()
    return Fernet(key.encode())


def encrypt_text(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_text(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise SecretDecryptError(
            "stored secret could not be decrypted — was FERNET_KEY changed?"
        ) from exc
