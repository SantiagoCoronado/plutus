"""Point-in-time metric panels for screen backtests.

For every backtestable field this builds a DataFrame of dates × symbols whose value
at date t equals what compute_snapshot would have reported on data up to t — the
"as-if-you-screened-that-night" view. Definitions deliberately mirror
app/analysis/indicators.py; tests/unit/test_backtest_panel.py locks the two together.

LOOK-AHEAD BIAS WARNING (spec §5.4 guardrail): every series here may only use data
at or before t. That means rolling/shift discipline only — reviewers must reject any
negative shift, centered window, or full-series normalization added to this module.
Missing bars stay NaN and are never forward-filled: a stale metric that would be
NULL in live screening must also be NA here (Kleene semantics then exclude the
asset, exactly like SQL does).
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.data import load_ohlcv_frame
from app.analysis.indicators import (
    INDICATORS,
    RETURN_OFFSETS,
    RS_OFFSETS,
    TRADING_DAYS,
    compute_series,
)
from app.models import Asset

# First bar of the panel calendar eligible for trading: covers sma_200 (200),
# return_1y/52w windows (252), and ADX stabilization, with margin.
WARMUP_BARS = 300

# calendar days of extra history loaded before the requested start so that the
# panel has >= WARMUP_BARS bars before the first rebalance (300 trading days
# is ~435 calendar days; 650 leaves slack for holidays and listing gaps)
WARMUP_CALENDAR_DAYS = 650

# indicator output column -> registry key (e.g. "macd_hist" -> "macd")
COLUMN_TO_SPEC: dict[str, str] = {
    col: spec.key for spec in INDICATORS.values() for col in spec.output_columns()
}

_RAW_FIELDS = ("close", "volume")


def build_field_panel(
    session: Session,
    assets: list[Asset],
    fields: set[str],
    start: date,
    end: date,
    benchmark_symbol: str | None,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame]:
    """Returns ({field: DataFrame(dates × symbols)}, close_panel, open_panel).

    The calendar is the union of the universe's bar dates (one asset class per
    run keeps it coherent); assets without a bar on a date hold NaN everywhere.
    """
    load_start = start - timedelta(days=WARMUP_CALENDAR_DAYS)
    frames: dict[str, pd.DataFrame] = {}
    for asset in assets:
        df = load_ohlcv_frame(session, asset.id, "1d", start=load_start, end=end)
        if not df.empty:
            frames[asset.symbol] = df
    if not frames:
        raise ValueError("no OHLCV history for any asset in the universe")

    benchmark_close = _load_benchmark_close(
        session, benchmark_symbol, load_start, end
    ) if fields & set(RS_OFFSETS) else None

    calendar = frames[next(iter(frames))].index
    for df in frames.values():
        calendar = calendar.union(df.index)

    per_asset: dict[str, dict[str, pd.Series]] = {
        symbol: _asset_field_series(df, fields, benchmark_close)
        for symbol, df in frames.items()
    }
    symbols = sorted(frames)
    panels = {
        field: pd.DataFrame(
            {symbol: per_asset[symbol][field] for symbol in symbols}, index=calendar
        )
        for field in fields
    }
    close_panel = pd.DataFrame(
        {symbol: frames[symbol]["close"] for symbol in symbols}, index=calendar
    )
    open_panel = pd.DataFrame(
        {symbol: frames[symbol]["open"] for symbol in symbols}, index=calendar
    )
    return panels, close_panel, open_panel


def _load_benchmark_close(
    session: Session, symbol: str | None, start: date, end: date
) -> pd.Series | None:
    if symbol is None:
        return None
    asset = session.scalars(
        select(Asset).where(Asset.symbol == symbol, Asset.is_active).order_by(Asset.id)
    ).first()
    if asset is None:
        return None
    df = load_ohlcv_frame(session, asset.id, "1d", start=start, end=end)
    return None if df.empty else df["close"].astype(float)


def _asset_field_series(
    df: pd.DataFrame, fields: set[str], benchmark_close: pd.Series | None
) -> dict[str, pd.Series]:
    """Every requested field as a per-bar series over df's index (NaN before warmup)."""
    close = df["close"].astype(float)
    nan = pd.Series(np.nan, index=df.index)
    out: dict[str, pd.Series] = {}

    indicator_keys = sorted(
        {COLUMN_TO_SPEC[f] for f in fields if f in COLUMN_TO_SPEC}
    )
    indicator_df = compute_series(df, indicator_keys) if indicator_keys else None

    for field in fields:
        if field in _RAW_FIELDS:
            out[field] = df[field].astype(float) if field in df else nan
        elif field == "volume_avg_20":
            out[field] = df["volume"].rolling(20).mean() if "volume" in df else nan
        elif field in RETURN_OFFSETS:
            out[field] = close / close.shift(RETURN_OFFSETS[field]) - 1
        elif field == "return_ytd":
            out[field] = _return_ytd_series(close)
        elif field in COLUMN_TO_SPEC:
            out[field] = (
                indicator_df[field]
                if indicator_df is not None and field in indicator_df
                else nan
            )
        elif field in ("high_52w", "low_52w", "dist_52w_high", "dist_52w_low"):
            out[field] = _series_52w(df, close, field)
        elif field in RS_OFFSETS:
            out[field] = _rs_series(close, benchmark_close, RS_OFFSETS[field])
        else:  # non-PIT fields are rejected upstream by parse_ast(BACKTESTABLE_FIELDS)
            raise KeyError(f"field {field!r} is not point-in-time computable")
    return out


def _return_ytd_series(close: pd.Series) -> pd.Series:
    """close(t) vs the last close of any prior calendar year, as known at t."""
    years = close.index.year
    prior_year_close: dict[int, float] = {}
    for year in sorted(set(years)):
        earlier = close[years < year]
        prior_year_close[year] = float(earlier.iloc[-1]) if not earlier.empty else np.nan
    baseline = pd.Series([prior_year_close[y] for y in years], index=close.index)
    return close / baseline - 1


def _series_52w(df: pd.DataFrame, close: pd.Series, field: str) -> pd.Series:
    # mirrors compute_snapshot: window = min(252, bars so far), only once >= 60 bars
    high = df["high"].astype(float).rolling(TRADING_DAYS, min_periods=60).max()
    low = df["low"].astype(float).rolling(TRADING_DAYS, min_periods=60).min()
    match field:
        case "high_52w":
            return high
        case "low_52w":
            return low
        case "dist_52w_high":
            return close / high - 1
        case "dist_52w_low":
            return close / low - 1
    raise KeyError(field)


def _rs_series(
    close: pd.Series, benchmark_close: pd.Series | None, offset: int
) -> pd.Series:
    """Trailing-return difference vs the benchmark on their common (aligned) dates."""
    if benchmark_close is None:
        return pd.Series(np.nan, index=close.index)
    aligned = pd.concat(
        [close.rename("asset"), benchmark_close.rename("bench")], axis=1, join="inner"
    ).dropna()
    if aligned.empty:
        return pd.Series(np.nan, index=close.index)
    asset_ret = aligned["asset"] / aligned["asset"].shift(offset) - 1
    bench_ret = aligned["bench"] / aligned["bench"].shift(offset) - 1
    return (asset_ret - bench_ret).reindex(close.index)
