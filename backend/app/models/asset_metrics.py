from datetime import date, datetime

from sqlalchemy import DateTime, ForeignKey, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# All nullable double-precision metric columns — single source of truth for the
# migration, the nightly upsert set_, and the Phase 3 screener whitelist.
# test_models_consistency asserts this tuple matches the mapped table exactly.
METRIC_COLUMNS: tuple[str, ...] = (
    "close", "volume", "volume_avg_20",
    "return_1d", "return_1w", "return_1m", "return_3m", "return_6m", "return_ytd", "return_1y",
    "sma_20", "sma_50", "sma_200", "ema_12", "ema_26", "ema_50", "wma_20",
    "rsi_14",
    "macd", "macd_signal", "macd_hist",
    "bb_upper", "bb_middle", "bb_lower", "bb_width", "percent_b",
    "atr_14", "atr_pct",
    "adx_14", "plus_di_14", "minus_di_14",
    "stoch_k", "stoch_d",
    "obv", "vwap_20",
    "volatility_20", "volatility_60",
    "high_52w", "low_52w", "dist_52w_high", "dist_52w_low",
    "rs_1m", "rs_3m", "rs_6m",
    "market_cap", "pe", "ps", "ev_ebitda",
    "gross_margin", "net_margin", "roe", "debt_to_equity", "revenue_growth_yoy",
)  # fmt: skip


class AssetMetrics(Base):
    """Latest nightly indicator + fundamental snapshot, one row per asset (spec §5.2/5.3).

    Normalized columns on purpose: the Phase 3 screener AST whitelists column names,
    stays typed and indexable. `extras` carries the per-class long tail
    (mcap_rank, circulating_supply, bars_available).
    """

    __tablename__ = "asset_metrics"

    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True
    )
    as_of: Mapped[date]
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    benchmark_symbol: Mapped[str | None] = mapped_column(Text)
    extras: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))

    # price/volume
    close: Mapped[float | None]
    volume: Mapped[float | None]
    volume_avg_20: Mapped[float | None]
    # returns
    return_1d: Mapped[float | None]
    return_1w: Mapped[float | None]
    return_1m: Mapped[float | None]
    return_3m: Mapped[float | None]
    return_6m: Mapped[float | None]
    return_ytd: Mapped[float | None]
    return_1y: Mapped[float | None]
    # moving averages
    sma_20: Mapped[float | None]
    sma_50: Mapped[float | None]
    sma_200: Mapped[float | None]
    ema_12: Mapped[float | None]
    ema_26: Mapped[float | None]
    ema_50: Mapped[float | None]
    wma_20: Mapped[float | None]
    # oscillators / trend
    rsi_14: Mapped[float | None]
    macd: Mapped[float | None]
    macd_signal: Mapped[float | None]
    macd_hist: Mapped[float | None]
    bb_upper: Mapped[float | None]
    bb_middle: Mapped[float | None]
    bb_lower: Mapped[float | None]
    bb_width: Mapped[float | None]
    percent_b: Mapped[float | None]
    atr_14: Mapped[float | None]
    atr_pct: Mapped[float | None]
    adx_14: Mapped[float | None]
    plus_di_14: Mapped[float | None]
    minus_di_14: Mapped[float | None]
    stoch_k: Mapped[float | None]
    stoch_d: Mapped[float | None]
    obv: Mapped[float | None]
    vwap_20: Mapped[float | None]
    # volatility / ranges
    volatility_20: Mapped[float | None]
    volatility_60: Mapped[float | None]
    high_52w: Mapped[float | None]
    low_52w: Mapped[float | None]
    dist_52w_high: Mapped[float | None]
    dist_52w_low: Mapped[float | None]
    # relative strength vs benchmark
    rs_1m: Mapped[float | None]
    rs_3m: Mapped[float | None]
    rs_6m: Mapped[float | None]
    # fundamental snapshot (stocks/ETFs; NULL otherwise)
    market_cap: Mapped[float | None]
    pe: Mapped[float | None]
    ps: Mapped[float | None]
    ev_ebitda: Mapped[float | None]
    gross_margin: Mapped[float | None]
    net_margin: Mapped[float | None]
    roe: Mapped[float | None]
    debt_to_equity: Mapped[float | None]
    revenue_growth_yoy: Mapped[float | None]
