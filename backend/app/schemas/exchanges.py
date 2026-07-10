from datetime import datetime
from typing import Any

from pydantic import BaseModel


class BitsoKeysIn(BaseModel):
    """Write-only credential update; omitted fields are left unchanged."""

    api_key: str | None = None
    api_secret: str | None = None


class ExchangeRunOut(BaseModel):
    status: str
    trades_created: int
    trades_skipped: int
    finished_at: datetime | None
    details: dict[str, Any] | None


class ExchangeAccountOut(BaseModel):
    account_id: int
    name: str
    provider: str | None
    last_synced_at: datetime | None
    last_status: str | None
    last_run: ExchangeRunOut | None
    # items the sync saw but could not land yet (pending status / untracked symbol)
    unresolved_skips: int = 0


class ExchangeStatusOut(BaseModel):
    configured: bool
    keys: dict[str, str | None]
    fernet_ready: bool
    accounts: list[ExchangeAccountOut]


class BitsoTestOut(BaseModel):
    ok: bool
    currencies: int | None = None
    error: str | None = None
