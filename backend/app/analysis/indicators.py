"""Indicator engine (spec §5.3).

One registry of IndicatorSpec drives both surfaces:
- compute_series(df, keys)  -> full per-bar series for chart overlays/subpanels
- compute_snapshot(...)     -> latest values for the nightly asset_metrics upsert

NaN policy: below min_bars, or a NaN latest value, snapshots store None —
never silently forward-fill. Volume-based indicators yield None when the frame
has no volume (forex).
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pandas_ta_classic as ta

TRADING_DAYS = 252

# bar offsets for point-in-time returns
RETURN_OFFSETS = {
    "return_1d": 1,
    "return_1w": 5,
    "return_1m": 21,
    "return_3m": 63,
    "return_6m": 126,
    "return_1y": 252,
}

RS_OFFSETS = {"rs_1m": 21, "rs_3m": 63, "rs_6m": 126}


@dataclass(frozen=True)
class IndicatorSpec:
    key: str
    min_bars: int
    compute: Callable[[pd.DataFrame], pd.DataFrame]
    requires_volume: bool = False
    columns: tuple[str, ...] = field(default=())

    def output_columns(self) -> tuple[str, ...]:
        return self.columns or (self.key,)


def _single(name: str, fn: Callable[[pd.DataFrame], pd.Series]):
    def compute(df: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({name: fn(df)})

    return compute


def _macd(df: pd.DataFrame) -> pd.DataFrame:
    raw = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if raw is None:
        return pd.DataFrame(index=df.index)
    cols = list(raw.columns)  # MACD_12_26_9, MACDh_12_26_9, MACDs_12_26_9
    mapping = {}
    for col in cols:
        if col.startswith("MACDh"):
            mapping[col] = "macd_hist"
        elif col.startswith("MACDs"):
            mapping[col] = "macd_signal"
        elif col.startswith("MACD"):
            mapping[col] = "macd"
    return raw.rename(columns=mapping)[["macd", "macd_signal", "macd_hist"]]


def _bbands(df: pd.DataFrame) -> pd.DataFrame:
    raw = ta.bbands(df["close"], length=20, std=2)
    if raw is None:
        return pd.DataFrame(index=df.index)
    mapping = {}
    for col in raw.columns:
        if col.startswith("BBL"):
            mapping[col] = "bb_lower"
        elif col.startswith("BBM"):
            mapping[col] = "bb_middle"
        elif col.startswith("BBU"):
            mapping[col] = "bb_upper"
        elif col.startswith("BBB"):
            mapping[col] = "bb_width"
        elif col.startswith("BBP"):
            mapping[col] = "percent_b"
    out = raw.rename(columns=mapping)
    return out[[c for c in ("bb_upper", "bb_middle", "bb_lower", "bb_width", "percent_b") if c in out]]


def _atr(df: pd.DataFrame) -> pd.DataFrame:
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    return pd.DataFrame({"atr_14": atr, "atr_pct": atr / df["close"]})


def _adx(df: pd.DataFrame) -> pd.DataFrame:
    raw = ta.adx(df["high"], df["low"], df["close"], length=14)
    if raw is None:
        return pd.DataFrame(index=df.index)
    mapping = {}
    for col in raw.columns:
        if col.startswith("ADX"):
            mapping[col] = "adx_14"
        elif col.startswith("DMP"):
            mapping[col] = "plus_di_14"
        elif col.startswith("DMN"):
            mapping[col] = "minus_di_14"
    return raw.rename(columns=mapping)[["adx_14", "plus_di_14", "minus_di_14"]]


def _stoch(df: pd.DataFrame) -> pd.DataFrame:
    raw = ta.stoch(df["high"], df["low"], df["close"], k=14, d=3, smooth_k=3)
    if raw is None:
        return pd.DataFrame(index=df.index)
    mapping = {}
    for col in raw.columns:
        if col.startswith("STOCHk"):
            mapping[col] = "stoch_k"
        elif col.startswith("STOCHd"):
            mapping[col] = "stoch_d"
    return raw.rename(columns=mapping)[["stoch_k", "stoch_d"]]


def _vwap_20(df: pd.DataFrame) -> pd.DataFrame:
    # rolling 20d typical-price VWAP — true VWAP is intraday/session-anchored;
    # labeled "VWAP (20d rolling)" in the UI (deliberate definitional choice)
    tp = (df["high"] + df["low"] + df["close"]) / 3
    pv = (tp * df["volume"]).rolling(20).sum()
    v = df["volume"].rolling(20).sum()
    return pd.DataFrame({"vwap_20": pv / v})


def _volatility(n: int):
    def compute(df: pd.DataFrame) -> pd.DataFrame:
        log_ret = np.log(df["close"].astype(float)).diff()
        return pd.DataFrame(
            {f"volatility_{n}": log_ret.rolling(n).std() * math.sqrt(TRADING_DAYS)}
        )

    return compute


INDICATORS: dict[str, IndicatorSpec] = {
    spec.key: spec
    for spec in [
        IndicatorSpec("sma_20", 20, _single("sma_20", lambda df: ta.sma(df["close"], length=20))),
        IndicatorSpec("sma_50", 50, _single("sma_50", lambda df: ta.sma(df["close"], length=50))),
        IndicatorSpec(
            "sma_200", 200, _single("sma_200", lambda df: ta.sma(df["close"], length=200))
        ),
        IndicatorSpec("wma_20", 20, _single("wma_20", lambda df: ta.wma(df["close"], length=20))),
        IndicatorSpec("ema_12", 12, _single("ema_12", lambda df: ta.ema(df["close"], length=12))),
        IndicatorSpec("ema_26", 26, _single("ema_26", lambda df: ta.ema(df["close"], length=26))),
        IndicatorSpec("ema_50", 50, _single("ema_50", lambda df: ta.ema(df["close"], length=50))),
        IndicatorSpec("rsi_14", 15, _single("rsi_14", lambda df: ta.rsi(df["close"], length=14))),
        IndicatorSpec("macd", 35, _macd, columns=("macd", "macd_signal", "macd_hist")),
        IndicatorSpec(
            "bbands",
            20,
            _bbands,
            columns=("bb_upper", "bb_middle", "bb_lower", "bb_width", "percent_b"),
        ),
        IndicatorSpec("atr_14", 15, _atr, columns=("atr_14", "atr_pct")),
        # ADX: double Wilder smoothing — values stabilize only after ~40 bars
        IndicatorSpec("adx_14", 28, _adx, columns=("adx_14", "plus_di_14", "minus_di_14")),
        IndicatorSpec("stoch", 17, _stoch, columns=("stoch_k", "stoch_d")),
        IndicatorSpec(
            "obv", 2, _single("obv", lambda df: ta.obv(df["close"], df["volume"])),
            requires_volume=True,
        ),
        IndicatorSpec("vwap_20", 20, _vwap_20, requires_volume=True),
        IndicatorSpec("volatility_20", 21, _volatility(20), columns=("volatility_20",)),
        IndicatorSpec("volatility_60", 61, _volatility(60), columns=("volatility_60",)),
    ]
}

# what the chart's /indicators endpoint accepts (snapshot-only metrics excluded)
SERIES_KEYS: tuple[str, ...] = tuple(INDICATORS.keys())


def _has_volume(df: pd.DataFrame) -> bool:
    return "volume" in df.columns and df["volume"].notna().any()


def _last(series: pd.Series) -> float | None:
    if series is None or len(series) == 0:
        return None
    value = series.iloc[-1]
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return float(value)


def compute_series(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Per-bar series for the requested indicator keys, aligned to df's index."""
    unknown = [k for k in keys if k not in INDICATORS]
    if unknown:
        raise KeyError(f"unknown indicator keys: {unknown}; valid: {sorted(INDICATORS)}")
    parts = []
    for key in keys:
        spec = INDICATORS[key]
        if spec.requires_volume and not _has_volume(df):
            continue
        if len(df) < spec.min_bars:
            continue
        parts.append(spec.compute(df))
    if not parts:
        return pd.DataFrame(index=df.index)
    return pd.concat(parts, axis=1)


def compute_snapshot(
    df: pd.DataFrame,
    benchmark_df: pd.DataFrame | None = None,
    benchmark_symbol: str | None = None,
) -> dict | None:
    """Latest-value snapshot for asset_metrics. Returns None for an empty frame."""
    if df.empty:
        return None
    close = df["close"].astype(float)
    n = len(df)
    out: dict = {
        "as_of": df.index[-1].date(),
        "close": _last(close),
        "volume": _last(df["volume"]) if _has_volume(df) else None,
        "volume_avg_20": (
            _last(df["volume"].rolling(20).mean()) if _has_volume(df) and n >= 20 else None
        ),
        "benchmark_symbol": None,
        "extras": {"bars_available": n},
    }

    # indicator registry: latest value of every output column
    for spec in INDICATORS.values():
        for col in spec.output_columns():
            out.setdefault(col, None)
        if spec.requires_volume and not _has_volume(df):
            continue
        if n < spec.min_bars:
            continue
        series_df = spec.compute(df)
        for col in spec.output_columns():
            if col in series_df:
                out[col] = _last(series_df[col])

    # point-in-time returns
    for name, offset in RETURN_OFFSETS.items():
        out[name] = (
            float(close.iloc[-1] / close.iloc[-1 - offset] - 1) if n > offset else None
        )
    out["return_ytd"] = _return_ytd(close)

    # 52-week range on up to the last 252 bars (never below 60 bars of history)
    out.update(
        {"high_52w": None, "low_52w": None, "dist_52w_high": None, "dist_52w_low": None}
    )
    if n >= 60:
        window = min(TRADING_DAYS, n)
        high_52w = float(df["high"].iloc[-window:].max())
        low_52w = float(df["low"].iloc[-window:].min())
        last_close = float(close.iloc[-1])
        out["high_52w"] = high_52w
        out["low_52w"] = low_52w
        out["dist_52w_high"] = last_close / high_52w - 1 if high_52w else None
        out["dist_52w_low"] = last_close / low_52w - 1 if low_52w else None
        out["extras"]["bars_in_52w_window"] = window

    # relative strength vs benchmark: return difference on ts-aligned closes
    out.update({"rs_1m": None, "rs_3m": None, "rs_6m": None})
    if benchmark_df is not None and not benchmark_df.empty:
        aligned = pd.concat(
            [close.rename("asset"), benchmark_df["close"].astype(float).rename("bench")],
            axis=1,
            join="inner",
        ).dropna()
        m = len(aligned)
        for name, offset in RS_OFFSETS.items():
            if m > offset:
                asset_ret = aligned["asset"].iloc[-1] / aligned["asset"].iloc[-1 - offset] - 1
                bench_ret = aligned["bench"].iloc[-1] / aligned["bench"].iloc[-1 - offset] - 1
                out[name] = float(asset_ret - bench_ret)
        if any(out[name] is not None for name in RS_OFFSETS):
            out["benchmark_symbol"] = benchmark_symbol

    return out


def _return_ytd(close: pd.Series) -> float | None:
    current_year = close.index[-1].year
    prior = close[close.index.year < current_year]
    if prior.empty:
        return None
    return float(close.iloc[-1] / prior.iloc[-1] - 1)
