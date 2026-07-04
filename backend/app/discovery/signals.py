"""Signal library for the discovery engine.

Each signal scores one asset from its daily bars (plus optional context) on a 0-100
scale and, where the signal has a per-bar definition, returns the full historical
trigger mask — the engine reuses that mask as the "history check" input (forward
returns after past triggers), so signal math and its own track record can never drift.

Indicator series come from the shared engine (`app.analysis.indicators.compute_series`)
— the same math that feeds charts, the nightly snapshot, and the screener.

Deferred signals (no data source on our free provider tiers, revisit later):
- earnings surprise (needs consensus-estimate history)
- forex rate-differential shift (needs central-bank policy-rate series, e.g. FRED)
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.analysis.indicators import compute_series

ALL_CLASSES = ("stock", "etf", "crypto", "forex")
VOLUME_CLASSES = ("stock", "etf", "crypto")

# breakout: new high over the prior N bars
BREAKOUT_WINDOW = 55
# volume z-score window (bars), computed against the prior window (shifted)
VOLUME_Z_WINDOW = 60
# ma_cross: a golden cross this many bars back still counts as fresh
CROSS_MAX_AGE = 10
# momentum_rank needs at least this many ranked peers to be meaningful
MIN_MOMENTUM_PEERS = 10
# valuation_anomaly needs at least this many positive annual data points per metric
MIN_VALUATION_HISTORY = 3


def clip01(x: float) -> float:
    return min(max(x, 0.0), 1.0)


def _safe(x: Any, digits: int = 4) -> float | None:
    """JSON-safe evidence value: plain rounded float, None for NaN/missing."""
    if x is None:
        return None
    x = float(x)
    if np.isnan(x) or np.isinf(x):
        return None
    return round(x, digits)


@dataclass(frozen=True)
class SignalResult:
    score: float  # 0-100
    triggered: bool
    evidence: dict[str, Any]
    # full per-bar trigger history (True on trigger bars); None when the signal
    # has no daily definition (cross-sectional or annual-data signals)
    mask: pd.Series | None = None


@dataclass(frozen=True)
class SignalSpec:
    key: str
    label: str
    description: str
    asset_classes: tuple[str, ...]
    min_bars: int
    compute: Callable[[pd.DataFrame, Mapping[str, Any]], SignalResult | None]
    requires_volume: bool = False
    cross_sectional: bool = False
    supports_history_check: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


def _volume_z(df: pd.DataFrame) -> pd.Series | None:
    """Volume z-score vs the trailing window (shifted: today never inflates its own baseline)."""
    volume = df["volume"]
    if volume.isna().all():
        return None
    prior = volume.shift(1)
    mean = prior.rolling(VOLUME_Z_WINDOW).mean()
    std = prior.rolling(VOLUME_Z_WINDOW).std()
    return (volume - mean) / std


def _breakout(df: pd.DataFrame, ctx: Mapping[str, Any]) -> SignalResult | None:
    close = df["close"]
    prior_high = close.shift(1).rolling(BREAKOUT_WINDOW).max()
    if pd.isna(prior_high.iloc[-1]) or pd.isna(close.iloc[-1]):
        return None
    at_high = close > prior_high  # strictly above: a flat series is not "breaking out"
    vol_z = _volume_z(df)

    mask = at_high if vol_z is None else at_high & (vol_z >= 1)
    mask = mask.fillna(False)

    latest_z = None if vol_z is None else vol_z.iloc[-1]
    if bool(at_high.iloc[-1]):
        vol_part = 0.0 if latest_z is None or pd.isna(latest_z) else clip01(latest_z / 3)
        score = 70 + 30 * vol_part
    else:
        dist = float(close.iloc[-1] / prior_high.iloc[-1] - 1)  # negative below the high
        score = 70 * clip01(1 + dist / 0.10)
    return SignalResult(
        score=round(score, 1),
        triggered=bool(mask.iloc[-1]),
        evidence={
            "distance_from_high": _safe(close.iloc[-1] / prior_high.iloc[-1] - 1),
            "volume_z": _safe(latest_z),
        },
        mask=mask,
    )


def _ma_cross(df: pd.DataFrame, ctx: Mapping[str, Any]) -> SignalResult | None:
    series = compute_series(df, ["sma_50", "sma_200"])
    if "sma_50" not in series or "sma_200" not in series:
        return None
    sma_50, sma_200 = series["sma_50"], series["sma_200"]
    if pd.isna(sma_200.iloc[-1]):
        return None
    above = (sma_50 > sma_200).fillna(False)
    mask = above & ~above.shift(1, fill_value=False)  # cross-up bars only

    score = 0.0
    bars_since = None
    if bool(above.iloc[-1]) and bool(mask.any()):
        bars_since = int(len(mask) - 1 - np.flatnonzero(mask.to_numpy())[-1])
        score = 100 * clip01(1 - bars_since / 40)
    return SignalResult(
        score=round(score, 1),
        triggered=bars_since is not None and bars_since <= CROSS_MAX_AGE,
        evidence={
            "bars_since_cross": bars_since,
            "sma_50": _safe(sma_50.iloc[-1]),
            "sma_200": _safe(sma_200.iloc[-1]),
        },
        mask=mask,
    )


def _rsi_extreme(df: pd.DataFrame, ctx: Mapping[str, Any]) -> SignalResult | None:
    series = compute_series(df, ["rsi_14"])
    if "rsi_14" not in series:
        return None
    rsi = series["rsi_14"]
    latest = rsi.iloc[-1]
    if pd.isna(latest):
        return None
    mask = (rsi <= 30).fillna(False)
    return SignalResult(
        score=round(100 * clip01((35 - float(latest)) / 25), 1),
        triggered=bool(mask.iloc[-1]),
        evidence={"rsi_14": _safe(latest)},
        mask=mask,
    )


def _momentum_rank(df: pd.DataFrame, ctx: Mapping[str, Any]) -> SignalResult | None:
    """Cross-sectional: the engine computes this asset's momentum percentile vs the
    resolved universe (mean of available 3m/6m/1y returns) and passes it in ctx."""
    percentile = ctx.get("momentum_percentile")
    if percentile is None:
        return None
    return SignalResult(
        score=round(100 * float(percentile), 1),
        triggered=float(percentile) >= 0.8,
        evidence={
            "percentile": _safe(percentile),
            "momentum": _safe(ctx.get("momentum_value")),
            "peers": ctx.get("momentum_peers"),
        },
        mask=None,
    )


def _mean_reversion(df: pd.DataFrame, ctx: Mapping[str, Any]) -> SignalResult | None:
    close = df["close"]
    series = compute_series(df, ["sma_50"])
    if "sma_50" not in series:
        return None
    sma_50 = series["sma_50"]
    std_50 = close.rolling(50).std()
    z = (close - sma_50) / std_50
    latest = z.iloc[-1]
    if pd.isna(latest):
        return None
    mask = (z <= -2).fillna(False)
    return SignalResult(
        score=round(100 * clip01((-float(latest) - 1) / 2), 1),
        triggered=bool(mask.iloc[-1]),
        evidence={"z_score": _safe(latest), "sma_50": _safe(sma_50.iloc[-1])},
        mask=mask,
    )


def _valuation_anomaly(df: pd.DataFrame, ctx: Mapping[str, Any]) -> SignalResult | None:
    """Latest valuation vs the asset's own annual history (5y of fundamentals rows).
    Cheaper than most of its own past -> high score. No daily trigger series."""
    current: Mapping[str, Any] = ctx.get("valuation_current") or {}
    history: Mapping[str, list[float]] = ctx.get("valuation_history") or {}

    parts: dict[str, float] = {}
    evidence: dict[str, Any] = {}
    for metric in ("pe", "ps"):
        now_value = current.get(metric)
        past = [v for v in history.get(metric, []) if v is not None and v > 0]
        if now_value is None or now_value <= 0 or len(past) < MIN_VALUATION_HISTORY:
            continue
        rank = sum(1 for v in past if v < now_value) / len(past)
        parts[metric] = 100 * (1 - rank)
        evidence[metric] = {"current": _safe(now_value), "history_median": _safe(np.median(past))}
    if not parts:
        return None
    score = sum(parts.values()) / len(parts)
    return SignalResult(
        score=round(score, 1),
        triggered=score >= 75,
        evidence=evidence,
        mask=None,
    )


def _volume_anomaly(df: pd.DataFrame, ctx: Mapping[str, Any]) -> SignalResult | None:
    vol_z = _volume_z(df)
    if vol_z is None:
        return None
    latest = vol_z.iloc[-1]
    if pd.isna(latest):
        return None
    mask = (vol_z >= 3).fillna(False)
    return SignalResult(
        score=round(100 * clip01(float(latest) / 5), 1),
        triggered=bool(mask.iloc[-1]),
        evidence={"volume_z": _safe(latest)},
        mask=mask,
    )


def _crypto_drawdown(df: pd.DataFrame, ctx: Mapping[str, Any]) -> SignalResult | None:
    close = df["close"]
    drawdown = close / close.cummax() - 1
    latest_dd = drawdown.iloc[-1]
    if pd.isna(latest_dd):
        return None
    vol_z = _volume_z(df)
    latest_z = None if vol_z is None else vol_z.iloc[-1]

    dd_mask = drawdown <= -0.5
    mask = (dd_mask if vol_z is None else dd_mask & (vol_z >= 1)).fillna(False)
    vol_part = 0.0 if latest_z is None or pd.isna(latest_z) else clip01(float(latest_z) / 3)
    score = 60 * clip01((-float(latest_dd) - 0.3) / 0.6) + 40 * vol_part
    return SignalResult(
        score=round(score, 1),
        triggered=bool(mask.iloc[-1]),
        evidence={"drawdown_from_high": _safe(latest_dd), "volume_z": _safe(latest_z)},
        mask=mask,
    )


def _pullback(df: pd.DataFrame, ctx: Mapping[str, Any]) -> SignalResult | None:
    close = df["close"]
    series = compute_series(df, ["sma_20", "sma_50", "sma_200", "rsi_14"])
    for col in ("sma_20", "sma_50", "sma_200", "rsi_14"):
        if col not in series or pd.isna(series[col].iloc[-1]):
            return None
    sma_20, sma_50, sma_200 = series["sma_20"], series["sma_50"], series["sma_200"]
    rsi = series["rsi_14"]

    uptrend = (close > sma_200) & (sma_50 > sma_200)
    mask = (uptrend & (close < sma_20) & (rsi < 45)).fillna(False)

    score = 0.0
    if bool(uptrend.iloc[-1]):
        trend_part = clip01((float(sma_50.iloc[-1] / sma_200.iloc[-1]) - 1) / 0.10)
        dip_part = clip01((45 - float(rsi.iloc[-1])) / 20)
        score = 50 * trend_part + 50 * dip_part
    return SignalResult(
        score=round(score, 1),
        triggered=bool(mask.iloc[-1]),
        evidence={
            "rsi_14": _safe(rsi.iloc[-1]),
            "trend_strength": _safe(sma_50.iloc[-1] / sma_200.iloc[-1] - 1),
        },
        mask=mask,
    )


SIGNALS: dict[str, SignalSpec] = {
    spec.key: spec
    for spec in (
        SignalSpec(
            key="breakout",
            label="Price breakout",
            description=f"Close at a new {BREAKOUT_WINDOW}-day high, confirmed by unusual volume.",
            asset_classes=ALL_CLASSES,
            min_bars=120,
            compute=_breakout,
        ),
        SignalSpec(
            key="ma_cross",
            label="Trend cross (50/200)",
            description="50-day average crossed above the 200-day average recently.",
            asset_classes=ALL_CLASSES,
            min_bars=210,
            compute=_ma_cross,
        ),
        SignalSpec(
            key="rsi_extreme",
            label="Oversold (RSI)",
            description="14-day RSI at or below 30 — stretched to the downside.",
            asset_classes=ALL_CLASSES,
            min_bars=30,
            compute=_rsi_extreme,
        ),
        SignalSpec(
            key="momentum_rank",
            label="Momentum vs peers",
            description=(
                "Trailing 3/6/12-month returns ranked against the mandate's universe; "
                f"needs at least {MIN_MOMENTUM_PEERS} peers with data."
            ),
            asset_classes=ALL_CLASSES,
            min_bars=0,
            compute=_momentum_rank,
            cross_sectional=True,
            supports_history_check=False,
        ),
        SignalSpec(
            key="mean_reversion",
            label="Stretched below trend",
            description="Price two-plus standard deviations below its 50-day average.",
            asset_classes=ALL_CLASSES,
            min_bars=60,
            compute=_mean_reversion,
        ),
        SignalSpec(
            key="valuation_anomaly",
            label="Cheap vs own history",
            description="P/E and P/S low relative to the company's own five-year history.",
            asset_classes=("stock",),
            min_bars=0,
            compute=_valuation_anomaly,
            supports_history_check=False,
        ),
        SignalSpec(
            key="volume_anomaly",
            label="Unusual volume",
            description="Volume three-plus standard deviations above its 60-day norm.",
            asset_classes=VOLUME_CLASSES,
            min_bars=VOLUME_Z_WINDOW + 1,
            compute=_volume_anomaly,
            requires_volume=True,
        ),
        SignalSpec(
            key="crypto_drawdown",
            label="Far below the all-time high",
            description="Down 50%+ from the all-time high with volume picking up.",
            asset_classes=("crypto",),
            min_bars=250,
            compute=_crypto_drawdown,
        ),
        SignalSpec(
            key="pullback",
            label="Dip in an uptrend",
            description="Long-term uptrend intact, price dipping below the 20-day average.",
            asset_classes=ALL_CLASSES,
            min_bars=210,
            compute=_pullback,
        ),
    )
}


def applicable_signals(asset_class: str) -> list[SignalSpec]:
    return [spec for spec in SIGNALS.values() if asset_class in spec.asset_classes]


def composite_score(
    results: Mapping[str, SignalResult], weights: Mapping[str, float]
) -> float | None:
    """Weighted average over the signals that answered; None if none carry weight."""
    scored = [(results[key].score, w) for key, w in weights.items() if w > 0 and key in results]
    total_weight = sum(w for _, w in scored)
    if total_weight == 0:
        return None
    return round(sum(score * w for score, w in scored) / total_weight, 1)
