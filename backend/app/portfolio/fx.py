"""Currency conversion over the closes of tracked forex assets.

There is deliberately no fx_rates table: USDMXN and EURUSD are ordinary assets
ingested by the nightly forex job, so rates inherit the same idempotent
pipeline, backfill and compression as every other price series. A missing rate
(pair not backfilled yet) returns None — callers degrade with a warning, never
error out.

Pair convention: symbol "USDMXN" quotes MXN per 1 USD. Conversions triangulate
through USD when no direct or inverse pair exists (e.g. EUR→MXN).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Asset, Ohlcv

SUPPORTED_CURRENCIES = ("USD", "MXN", "EUR")


def fx_rate(session: Session, from_ccy: str, to_ccy: str, as_of: date) -> float | None:
    """Most recent conversion rate on or before `as_of` (1 from_ccy = X to_ccy)."""
    return fx_rate_with_age(session, from_ccy, to_ccy, as_of)[0]


def fx_rate_with_age(
    session: Session, from_ccy: str, to_ccy: str, as_of: date
) -> tuple[float | None, date | None]:
    """Like fx_rate, but also returns the close date the rate came from so
    callers can flag stale conversions. Triangulated rates report the OLDER leg
    — the staleness of a composite is bounded by its weakest link."""
    if from_ccy == to_ccy:
        return 1.0, as_of
    direct = _latest_close(session, f"{from_ccy}{to_ccy}", as_of)
    if direct is not None:
        return direct
    inverse = _latest_close(session, f"{to_ccy}{from_ccy}", as_of)
    if inverse is not None and inverse[0] != 0:
        return 1.0 / inverse[0], inverse[1]
    # triangulate through USD: EUR→MXN = EURUSD · USDMXN
    if "USD" not in (from_ccy, to_ccy):
        leg_a, date_a = fx_rate_with_age(session, from_ccy, "USD", as_of)
        leg_b, date_b = fx_rate_with_age(session, "USD", to_ccy, as_of)
        if leg_a is not None and leg_b is not None:
            return leg_a * leg_b, min(d for d in (date_a, date_b) if d is not None)
    return None, None


def fx_series(
    session: Session, from_ccy: str, to_ccy: str, start: date, end: date
) -> pd.Series:
    """Daily conversion-rate grid over [start, end], forward-filled across
    weekends/holidays. NaN before the first available close."""
    grid = pd.date_range(start, end, freq="D")
    if from_ccy == to_ccy:
        return pd.Series(1.0, index=grid)
    direct = _close_series(session, f"{from_ccy}{to_ccy}", start, end)
    if direct is not None:
        return align_to_grid(direct, grid)
    inverse = _close_series(session, f"{to_ccy}{from_ccy}", start, end)
    if inverse is not None:
        return 1.0 / align_to_grid(inverse, grid)
    if "USD" not in (from_ccy, to_ccy):
        leg_a = fx_series(session, from_ccy, "USD", start, end)
        leg_b = fx_series(session, "USD", to_ccy, start, end)
        return leg_a * leg_b
    return pd.Series(float("nan"), index=grid)


def align_to_grid(closes: pd.Series, grid: pd.DatetimeIndex) -> pd.Series:
    """Snap a sparse close series onto a daily grid, carrying the last known
    value forward. Pure — unit-tested without a database."""
    closes = closes.copy()
    closes.index = pd.DatetimeIndex(closes.index).normalize().tz_localize(None)
    closes = closes[~closes.index.duplicated(keep="last")].sort_index()
    return closes.reindex(grid, method="ffill")


def _forex_asset_id(session: Session, symbol: str) -> int | None:
    return session.execute(
        select(Asset.id).where(Asset.symbol == symbol, Asset.asset_class == "forex")
    ).scalar_one_or_none()


def _latest_close(session: Session, symbol: str, as_of: date) -> tuple[float, date] | None:
    asset_id = _forex_asset_id(session, symbol)
    if asset_id is None:
        return None
    row = session.execute(
        select(Ohlcv.close, Ohlcv.ts)
        .where(
            Ohlcv.asset_id == asset_id,
            Ohlcv.interval == "1d",
            Ohlcv.ts < pd.Timestamp(as_of).tz_localize("UTC") + pd.Timedelta(days=1),
        )
        .order_by(Ohlcv.ts.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    return row.close, row.ts.date()


def _close_series(
    session: Session, symbol: str, start: date, end: date
) -> pd.Series | None:
    asset_id = _forex_asset_id(session, symbol)
    if asset_id is None:
        return None
    rows = session.execute(
        select(Ohlcv.ts, Ohlcv.close)
        .where(
            Ohlcv.asset_id == asset_id,
            Ohlcv.interval == "1d",
            # reach back so the grid's first days can forward-fill
            Ohlcv.ts >= pd.Timestamp(start).tz_localize("UTC") - pd.Timedelta(days=14),
            Ohlcv.ts < pd.Timestamp(end).tz_localize("UTC") + pd.Timedelta(days=1),
        )
        .order_by(Ohlcv.ts)
    ).all()
    if not rows:
        return None
    return pd.Series([r.close for r in rows], index=pd.DatetimeIndex([r.ts for r in rows]))
