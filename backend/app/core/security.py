import secrets

from app.core.config import get_settings


def verify_token(candidate: str) -> bool:
    expected = get_settings().app_auth_token
    if not expected:
        return False
    return secrets.compare_digest(candidate, expected)
