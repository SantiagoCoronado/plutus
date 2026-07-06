"""Normalized shapes + Protocol every exchange connector fills.

Read-only by design: a connector fetches balances and history, never places or
cancels anything. Errors reuse the provider hierarchy so callers that already
catch ProviderError/ProviderAuthError (the HTTP client raises these) compose.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.providers.base import ProviderError


class ExchangeError(ProviderError):
    """An exchange request failed or returned a non-success payload."""


@dataclass(frozen=True)
class ExchangeBalance:
    currency: str
    total: float
    available: float
    locked: float


@dataclass(frozen=True)
class ExchangeTrade:
    """One fill. `major`/`minor` are signed (sell → negative major); the sync
    layer takes absolute values. `price` is the per-unit execution price."""

    tid: str
    book: str  # e.g. "btc_mxn": major=btc, minor=mxn
    side: str  # "buy" | "sell"
    major: float
    minor: float
    price: float
    fees_amount: float
    fees_currency: str
    created_at: datetime


@dataclass(frozen=True)
class ExchangeFunding:
    """A deposit onto the exchange (fiat cash-in or crypto received)."""

    fid: str
    currency: str
    amount: float
    status: str  # only "complete" items become transactions
    created_at: datetime
    method: str | None = None


@dataclass(frozen=True)
class ExchangeWithdrawal:
    """A withdrawal off the exchange (fiat cash-out or crypto sent)."""

    wid: str
    currency: str
    amount: float
    status: str
    created_at: datetime
    method: str | None = None


@runtime_checkable
class ExchangeConnector(Protocol):
    name: str

    def fetch_balances(self) -> list[ExchangeBalance]: ...

    def fetch_trades(self, since_tid: str | None = None) -> list[ExchangeTrade]: ...

    def fetch_fundings(self, since_id: str | None = None) -> list[ExchangeFunding]: ...

    def fetch_withdrawals(self, since_id: str | None = None) -> list[ExchangeWithdrawal]: ...
