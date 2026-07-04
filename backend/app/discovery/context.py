"""Auto-context for candidates: every inbox card ships its own evidence.

- history_check: forward returns after *past* triggers of the same signal on the
  same asset (the mask computed in signals.py), so the card can say "after 13 past
  signals: +3.1% median 20-day move, 62% win rate".
- snapshot: key metric values at trigger time.
- chart: downsampled recent closes for the card sparkline.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from app.backtest.perf import win_rate
from app.discovery.signals import SignalResult

HISTORY_HORIZONS = (5, 20, 60)  # forward-return horizons in bars
CHART_BARS = 120

SNAPSHOT_FIELDS = (
    "close",
    "return_1m",
    "return_1y",
    "rsi_14",
    "market_cap",
    "pe",
    "dist_52w_high",
)


def history_check(close: pd.Series, mask: pd.Series) -> dict[str, Any]:
    """Forward returns after historical trigger onsets (consecutive trigger runs
    collapse to their first bar; the live trigger on the final bar is excluded)."""
    onsets = (mask & ~mask.shift(1, fill_value=False)).to_numpy().copy()
    onsets[-1] = False  # the live trigger has no forward window yet
    idx = np.flatnonzero(onsets)
    prices = close.to_numpy(dtype=float)

    fwd: dict[str, Any] = {}
    for horizon in HISTORY_HORIZONS:
        valid = idx[idx + horizon < len(prices)]
        if len(valid) == 0:
            continue
        rets = prices[valid + horizon] / prices[valid] - 1
        rets = rets[~np.isnan(rets)]
        if len(rets) == 0:
            continue
        fwd[f"{horizon}d"] = {
            "n": int(len(rets)),
            "median": round(float(np.median(rets)), 4),
            "win_rate": win_rate([float(r) for r in rets]),
        }
    return {"n_triggers": int(len(idx)), "fwd": fwd}


def chart_payload(close: pd.Series) -> list[list[Any]]:
    """[[iso_date, close], ...] for the card sparkline. 6 significant digits —
    perf.downsample_curve rounds to cents, which flattens sub-dollar closes."""
    tail = close.tail(CHART_BARS).dropna()
    return [[ts.date().isoformat(), float(f"{float(v):.6g}")] for ts, v in tail.items()]


def build_context(
    close: pd.Series,
    results: Mapping[str, SignalResult],
    metrics: Mapping[str, Any] | None,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    if metrics:
        for field in SNAPSHOT_FIELDS:
            snapshot[field] = metrics.get(field)
        as_of = metrics.get("as_of")
        snapshot["as_of"] = as_of.isoformat() if as_of is not None else None
        volume, volume_avg = metrics.get("volume"), metrics.get("volume_avg_20")
        snapshot["volume_ratio"] = (
            round(volume / volume_avg, 2) if volume and volume_avg else None
        )

    checks = {
        key: history_check(close, result.mask)
        for key, result in results.items()
        if result.triggered and result.mask is not None
    }
    return {"snapshot": snapshot, "history_check": checks, "chart": chart_payload(close)}
