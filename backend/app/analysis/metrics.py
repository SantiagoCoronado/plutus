"""Nightly asset_metrics materialization (spec §5.2/5.3).

Runs after the EOD ingestion jobs (beat 03:40 local vs 03:00/03:10/03:20) and mirrors
eod.py's driver: per-asset try/except with per-asset commits, ingestion_runs logging,
success/partial/failed statuses. Idempotent by construction (PK upsert).
"""

from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.analysis.data import load_ohlcv_frame
from app.analysis.indicators import compute_snapshot
from app.core.config import get_settings
from app.core.db import SessionLocal, session_scope
from app.core.logging import get_logger
from app.ingestion.eod import _close_run, _open_run
from app.models import Asset, AssetMetrics, Fundamentals

log = get_logger(__name__)

FUNDAMENTAL_CLASSES = {"stock", "etf"}
FUNDAMENTAL_SNAPSHOT_COLUMNS = (
    "pe", "ps", "ev_ebitda", "gross_margin", "net_margin", "roe", "debt_to_equity",
)  # fmt: skip


def _benchmark_for_class(asset_class: str) -> str:
    settings = get_settings()
    if asset_class in ("stock", "etf"):
        return settings.benchmark_stock
    if asset_class == "crypto":
        return settings.benchmark_crypto
    return settings.benchmark_forex


def _load_benchmark_frames(session) -> dict[str, pd.DataFrame]:
    """One frame per distinct benchmark symbol, loaded once per run."""
    symbols = {
        _benchmark_for_class(cls) for cls in ("stock", "crypto", "forex")
    }
    frames: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        asset = session.scalar(select(Asset).where(Asset.symbol == symbol))
        if asset is not None:
            frames[symbol] = load_ohlcv_frame(session, asset.id)
    return frames


def _fundamental_snapshot(session, asset: Asset) -> dict:
    """Latest annual fundamentals + market cap for the snapshot columns."""
    out: dict = {}
    if asset.asset_class in FUNDAMENTAL_CLASSES:
        rows = session.scalars(
            select(Fundamentals)
            .where(Fundamentals.asset_id == asset.id, Fundamentals.period == "annual")
            .order_by(Fundamentals.report_date.desc())
            .limit(2)
        ).all()
        if rows:
            latest = rows[0]
            for col in FUNDAMENTAL_SNAPSHOT_COLUMNS:
                out[col] = getattr(latest, col)
            if len(rows) == 2 and rows[1].revenue:
                out["revenue_growth_yoy"] = float(latest.revenue / rows[1].revenue - 1) if latest.revenue else None
        profile = (asset.meta or {}).get("profile", {})
        if profile.get("market_cap"):
            out["market_cap"] = float(profile["market_cap"])
    return out


def _crypto_market_extras(session, assets: list[Asset]) -> dict[int, dict]:
    """One CoinGecko /coins/markets call for all tracked coins; failure never sinks the run."""
    coin_map = {
        a.provider_symbol_map.get("coingecko"): a.id
        for a in assets
        if a.asset_class == "crypto" and a.provider_symbol_map.get("coingecko")
    }
    if not coin_map:
        return {}
    try:
        from app.providers.registry import _build

        provider = _build("coingecko")
        markets = provider.get_markets(list(coin_map))
    except Exception as exc:  # noqa: BLE001
        log.warning("coingecko_markets_failed", error=str(exc))
        return {}
    extras: dict[int, dict] = {}
    for row in markets:
        asset_id = coin_map.get(row.get("id"))
        if asset_id is None:
            continue
        extras[asset_id] = {
            "market_cap": row.get("market_cap"),
            "mcap_rank": row.get("market_cap_rank"),
            "circulating_supply": row.get("circulating_supply"),
        }
    return extras


def _upsert_metrics(session, asset_id: int, snapshot: dict) -> None:
    values = {"asset_id": asset_id, **snapshot}
    stmt = pg_insert(AssetMetrics.__table__).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["asset_id"],
        set_={
            **{k: stmt.excluded[k] for k in values if k != "asset_id"},
            "computed_at": datetime.now(UTC),
        },
    )
    session.execute(stmt)


def run_metrics_refresh() -> int:
    """Recompute asset_metrics for every active asset. Returns ingestion_runs.id."""
    run_id = _open_run("metrics_refresh", None, None)
    ok = failed = written = 0
    errors: dict[str, str] = {}
    session = SessionLocal()
    try:
        assets = session.scalars(
            select(Asset).where(Asset.is_active).order_by(Asset.id)
        ).all()
        benchmark_frames = _load_benchmark_frames(session)
        crypto_extras = _crypto_market_extras(session, assets)

        for asset in assets:
            try:
                df = load_ohlcv_frame(session, asset.id)
                benchmark_symbol = _benchmark_for_class(asset.asset_class)
                benchmark_df = benchmark_frames.get(benchmark_symbol)
                if asset.symbol == benchmark_symbol:
                    benchmark_df, benchmark_symbol = None, None  # own benchmark -> NULL rs
                snapshot = compute_snapshot(
                    df, benchmark_df=benchmark_df, benchmark_symbol=benchmark_symbol
                )
                if snapshot is None:
                    log.info("metrics_skipped_no_bars", symbol=asset.symbol)
                    continue
                snapshot.update(_fundamental_snapshot(session, asset))
                if asset.id in crypto_extras:
                    extra = crypto_extras[asset.id]
                    if extra.get("market_cap"):
                        snapshot["market_cap"] = float(extra["market_cap"])
                    snapshot["extras"].update(
                        {k: extra[k] for k in ("mcap_rank", "circulating_supply")}
                    )
                _upsert_metrics(session, asset.id, snapshot)
                session.commit()
                ok += 1
                written += 1
                log.info("metrics_refreshed", symbol=asset.symbol, as_of=str(snapshot["as_of"]))
            except Exception as exc:  # noqa: BLE001 — one bad asset must not sink the run
                session.rollback()
                failed += 1
                errors[asset.symbol] = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("metrics_failed", symbol=asset.symbol, error=str(exc))
    finally:
        session.close()

    if failed == 0:
        status = "success"
    elif ok == 0:
        status = "failed"
    else:
        status = "partial"
    _close_run(run_id, status, written, ok, failed, {"errors": errors} if errors else {})
    return run_id


def main() -> None:
    from app.core.logging import configure_logging
    from app.models import IngestionRun

    configure_logging()
    run_id = run_metrics_refresh()
    with session_scope() as session:
        run = session.get(IngestionRun, run_id)
        print(
            f"run {run.id} [{run.job_name}] {run.status}: {run.rows_written} rows, "
            f"ok={run.symbols_ok}, failed={run.symbols_failed}"
            + (f", details={run.details}" if run.details else "")
        )


if __name__ == "__main__":
    main()
