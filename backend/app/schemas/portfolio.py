from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AccountType = Literal["brokerage", "exchange", "wallet", "bank", "manual"]
TransactionType = Literal[
    "buy",
    "sell",
    "deposit",
    "withdrawal",
    "dividend",
    "interest",
    "fee",
    "transfer_in",
    "transfer_out",
]
InvestmentKind = Literal["demand", "fixed_term"]
DayCount = Literal["act360", "act365"]
Compounding = Literal["daily", "monthly", "at_maturity"]
InvestmentStatus = Literal["active", "matured", "closed"]

CURRENCY_PATTERN = r"^[A-Z]{3}$"


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: AccountType
    provider: str | None = None
    currency: str = Field(default="USD", pattern=CURRENCY_PATTERN)
    note: str | None = None
    is_active: bool = True


class AccountPatch(BaseModel):
    is_active: bool | None = None


class CashBalanceOut(BaseModel):
    currency: str
    amount: float


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: str
    provider: str | None
    currency: str
    note: str | None
    is_active: bool
    created_at: datetime
    # computed by the route, not stored
    cash_balances: list[CashBalanceOut] = []
    transactions_count: int = 0
    bank_investments_count: int = 0


class LotLinkIn(BaseModel):
    buy_transaction_id: int
    quantity: float = Field(gt=0)


class TransactionIn(BaseModel):
    account_id: int
    asset_id: int | None = None
    type: TransactionType
    ts: datetime
    quantity: float = Field(gt=0)
    price: float | None = Field(default=None, ge=0)
    fees: float = Field(default=0, ge=0)
    currency: str = Field(pattern=CURRENCY_PATTERN)
    note: str | None = None
    # specific-ID sells only; omit for first-in-first-out
    lot_links: list[LotLinkIn] | None = None


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    asset_id: int | None
    type: str
    ts: datetime
    quantity: float
    price: float | None
    fees: float
    currency: str
    note: str | None
    external_id: str | None
    lot_links: list[dict[str, Any]] | None
    created_at: datetime
    # joined by the route
    account_name: str | None = None
    symbol: str | None = None


class TransactionListOut(BaseModel):
    items: list[TransactionOut]
    total: int


class RateTierIn(BaseModel):
    # null up_to = "everything above the previous tier"
    up_to: float | None = Field(default=None, gt=0)
    annual_rate: float = Field(ge=0)


class BankInvestmentIn(BaseModel):
    account_id: int
    name: str = Field(min_length=1, max_length=120)
    kind: InvestmentKind
    principal: float = Field(gt=0)
    currency: str = Field(default="MXN", pattern=CURRENCY_PATTERN)
    annual_rate: float = Field(ge=0)
    rate_tiers: list[RateTierIn] | None = None
    day_count: DayCount = "act360"
    compounding: Compounding = "at_maturity"
    start_date: date
    term_days: int | None = Field(default=None, gt=0)
    cap_amount: float | None = Field(default=None, gt=0)
    auto_renew: bool = False
    status: InvestmentStatus = "active"
    note: str | None = None


class BankInvestmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    name: str
    kind: str
    principal: float
    currency: str
    annual_rate: float
    rate_tiers: list[dict[str, Any]] | None
    day_count: str
    compounding: str
    start_date: date
    term_days: int | None
    maturity_date: date | None
    cap_amount: float | None
    auto_renew: bool
    status: str
    note: str | None
    created_at: datetime
    updated_at: datetime
    # computed by the route, not stored
    accrued_interest: float = 0.0
    current_value: float = 0.0
    projected_maturity_value: float | None = None
    days_to_maturity: int | None = None
    effective_annual_rate: float = 0.0
    account_name: str | None = None


class PositionOut(BaseModel):
    account_id: int
    account_name: str | None
    asset_id: int
    symbol: str
    name: str | None
    asset_class: str | None
    quantity: float
    average_cost_native: float | None
    native_currency: str
    last_price: float | None
    market_value_native: float | None
    value: float | None
    cost_basis: float | None
    unrealized_pnl: float | None
    unrealized_pnl_pct: float | None
    realized_pnl: float | None
    weight: float | None = None


class CashPositionOut(BaseModel):
    account_id: int
    account_name: str | None
    currency: str
    amount: float | None
    value: float | None


class BankPositionOut(BaseModel):
    id: int
    account_id: int
    account_name: str | None
    name: str
    kind: str
    currency: str
    principal: float
    accrued_interest: float | None
    value_native: float | None
    value: float | None
    maturity_date: date | None
    status: str


class PortfolioTotalsOut(BaseModel):
    value: float | None
    positions_value: float | None
    cash_value: float | None
    bank_value: float | None
    cost_basis: float | None
    unrealized_pnl: float | None
    unrealized_pnl_pct: float | None
    realized_pnl: float | None


class PositionsReportOut(BaseModel):
    as_of: date
    currency: str
    totals: PortfolioTotalsOut
    positions: list[PositionOut]
    cash: list[CashPositionOut]
    bank_investments: list[BankPositionOut]
    warnings: list[dict[str, Any]]


class BenchmarkSeriesOut(BaseModel):
    symbol: str
    indexed: list[list[Any]]


class PerformanceOut(BaseModel):
    currency: str
    period: str
    start: date
    end: date
    twr: float | None
    twr_annualized: float | None
    irr: float | None
    series: list[list[Any]]
    indexed: list[list[Any]]
    benchmark: BenchmarkSeriesOut | None
    flows: list[list[Any]]


class AllocationGroupOut(BaseModel):
    key: str
    value: float | None
    weight: float | None


class AllocationOut(BaseModel):
    as_of: date
    currency: str
    by: str
    total: float | None
    groups: list[AllocationGroupOut]
