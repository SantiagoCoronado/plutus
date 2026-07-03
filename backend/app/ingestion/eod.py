from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal, session_scope
from app.core.logging import get_logger
from app.ingestion.normalize import candles_to_rows
from app.models import Asset, IngestionRun, Ohlcv
from app.providers.base import MarketDataProvider, ProviderNotConfigured
from app.providers.registry import get_provider
from app.schemas.common import AssetClass, Interval

log = get_logger(__name__)

UPSERT_CHUNK = 1000

# "stock" jobs cover ETFs too — same provider, same pipeline
CLASS_GROUPS: dict[str, tuple[str, ...]] = {
    "stock": ("stock", "etf"),
    "crypto": ("crypto",),
    "forex": ("forex",),
}


def compute_window(session: Session, asset_id: int, interval: Interval) -> tuple[date, date] | None:
    """Incremental fetch window: day after the last stored bar, else the backfill horizon."""
    last_ts = session.scalar(
        select(func.max(Ohlcv.ts)).where(Ohlcv.asset_id == asset_id, Ohlcv.interval == interval)
    )
    today = datetime.now(UTC).date()
    if last_ts is None:
        start = today - timedelta(days=get_settings().initial_backfill_days)
    else:
        start = last_ts.date() + timedelta(days=1)
    if start > today:
        return None
    return start, today


def upsert_candles(session: Session, rows: list[dict]) -> int:
    """Idempotent by construction: conflicting (asset_id, interval, ts) rows are rewritten."""
    written = 0
    for i in range(0, len(rows), UPSERT_CHUNK):
        chunk = rows[i : i + UPSERT_CHUNK]
        stmt = pg_insert(Ohlcv.__table__).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["asset_id", "interval", "ts"],
            set_={c: stmt.excluded[c] for c in ("open", "high", "low", "close", "volume")},
        )
        session.execute(stmt)
        written += len(chunk)
    return written


def ingest_asset(
    session: Session,
    provider: MarketDataProvider,
    asset: Asset,
    interval: Interval = Interval.d1,
) -> int:
    window = compute_window(session, asset.id, interval)
    if window is None:
        return 0
    start, end = window
    provider_symbol = asset.provider_symbol_map.get(provider.name, asset.symbol)
    df = provider.get_ohlcv(provider_symbol, AssetClass(asset.asset_class), interval, start, end)
    return upsert_candles(session, candles_to_rows(df, asset.id, interval))


def _open_run(job_name: str, asset_class: str | None, provider: str | None) -> int:
    with session_scope() as session:
        run = IngestionRun(
            job_name=job_name, asset_class=asset_class, provider=provider, status="running"
        )
        session.add(run)
        session.flush()
        return run.id


def _close_run(
    run_id: int, status: str, rows_written: int, ok: int, failed: int, details: dict
) -> None:
    with session_scope() as session:
        run = session.get(IngestionRun, run_id)
        run.status = status
        run.finished_at = datetime.now(UTC)
        run.rows_written = rows_written
        run.symbols_ok = ok
        run.symbols_failed = failed
        run.details = details


def _ingest_assets(run_id: int, assets_query, provider_resolver) -> int:
    """Shared driver: per-asset try/except with per-asset commits so partial progress sticks."""
    rows_total = ok = failed = 0
    errors: dict[str, str] = {}
    session = SessionLocal()
    try:
        assets = session.scalars(assets_query).all()
        for asset in assets:
            try:
                provider = provider_resolver(asset)
                rows = ingest_asset(session, provider, asset)
                session.commit()
                rows_total += rows
                ok += 1
                log.info("ingested", symbol=asset.symbol, rows=rows)
            except Exception as exc:  # noqa: BLE001 — one bad symbol must not sink the run
                session.rollback()
                failed += 1
                errors[asset.symbol] = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("ingest_failed", symbol=asset.symbol, error=str(exc))
    finally:
        session.close()

    if failed == 0:
        status = "success"
    elif ok == 0:
        status = "failed"
    else:
        status = "partial"
    _close_run(run_id, status, rows_total, ok, failed, {"errors": errors} if errors else {})
    return run_id


def run_eod_ingestion(asset_class: str) -> int:
    """One EOD job per class group (what Celery Beat schedules). Returns ingestion_runs.id."""
    classes = CLASS_GROUPS[asset_class]
    try:
        provider = get_provider(classes[0])
        provider_name = provider.name
    except ProviderNotConfigured as exc:
        run_id = _open_run(f"eod_{asset_class}", asset_class, None)
        _close_run(run_id, "failed", 0, 0, 0, {"errors": {"_provider": str(exc)}})
        return run_id

    run_id = _open_run(f"eod_{asset_class}", asset_class, provider_name)
    query = (
        select(Asset)
        .where(Asset.is_active, Asset.asset_class.in_(classes))
        .order_by(Asset.id)
    )
    return _ingest_assets(run_id, query, lambda asset: provider)


def run_eod_all() -> list[int]:
    return [run_eod_ingestion(asset_class) for asset_class in ("crypto", "forex", "stock")]


def run_asset_backfill(asset_id: int) -> int:
    """Backfill one asset (enqueued when a new asset is tracked). Returns ingestion_runs.id."""
    run_id = _open_run("backfill", None, None)
    query = select(Asset).where(Asset.id == asset_id)
    return _ingest_assets(run_id, query, lambda asset: get_provider(asset.asset_class))
