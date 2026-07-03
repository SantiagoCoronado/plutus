from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import AssetClass, SymbolInfo


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    name: str
    asset_class: AssetClass
    exchange: str | None
    currency: str
    meta: dict = Field(default_factory=dict)
    is_active: bool
    created_at: datetime


class AssetCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)
    asset_class: AssetClass
    exchange: str | None = None
    currency: str = "USD"
    meta: dict = Field(default_factory=dict)


class SearchResultItem(BaseModel):
    symbol: str
    name: str
    asset_class: AssetClass
    exchange: str | None = None
    currency: str = "USD"
    tracked: bool = False
    asset_id: int | None = None
    provider: str | None = None
    provider_symbol: str | None = None

    @classmethod
    def from_symbol_info(cls, info: SymbolInfo) -> "SearchResultItem":
        return cls(
            symbol=info.symbol,
            name=info.name,
            asset_class=info.asset_class,
            exchange=info.exchange,
            currency=info.currency,
            provider=info.provider,
            provider_symbol=info.provider_symbol,
        )


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResultItem]
