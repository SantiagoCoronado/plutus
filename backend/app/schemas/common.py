from enum import StrEnum

from pydantic import BaseModel


class AssetClass(StrEnum):
    stock = "stock"
    etf = "etf"
    crypto = "crypto"
    forex = "forex"


class Interval(StrEnum):
    m1 = "1m"
    m5 = "5m"
    m15 = "15m"
    h1 = "1h"
    h4 = "4h"
    d1 = "1d"
    w1 = "1w"


class Quote(BaseModel):
    symbol: str
    price: float
    currency: str = "USD"
    ts: str | None = None


class SymbolInfo(BaseModel):
    symbol: str
    name: str
    asset_class: AssetClass
    exchange: str | None = None
    currency: str = "USD"
    provider: str
    provider_symbol: str
