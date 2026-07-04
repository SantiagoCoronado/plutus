from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.ingestion.eod import _close_run, _open_run
from app.models import FUNDAMENTAL_COLUMNS, Asset, Fundamentals
from app.providers.registry import get_fundamentals_provider

log = get_logger(__name__)

STATEMENT_CLASSES = ("stock",)  # ETFs get profile-only (FMP returns empty statements)
PROFILE_CLASSES = ("stock", "etf")

# FMP free tier: ~6 calls per stock against a 225/day budget. Each weekly run takes
# the stalest assets first (never-fetched, then oldest fetched_at), so the whole
# ~100-stock universe rotates through full coverage over ~3 weeks.
ASSETS_PER_RUN = 32


def upsert_fundamentals(session, asset_id: int, periods, provider_name: str) -> int:
    if not periods:
        return 0
    rows = [
        {
            "asset_id": asset_id,
            "period": p.period,
            "report_date": p.report_date,
            "fiscal_year": p.fiscal_year,
            "currency": p.currency,
            "provider": provider_name,
            "metrics": p.metrics,
            **{col: getattr(p, col) for col in FUNDAMENTAL_COLUMNS},
        }
        for p in periods
    ]
    stmt = pg_insert(Fundamentals.__table__).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["asset_id", "period", "report_date"],
        set_={
            **{col: stmt.excluded[col] for col in FUNDAMENTAL_COLUMNS},
            "metrics": stmt.excluded["metrics"],
            "fiscal_year": stmt.excluded["fiscal_year"],
            "currency": stmt.excluded["currency"],
            "provider": stmt.excluded["provider"],
            "fetched_at": stmt.excluded["fetched_at"],
        },
    )
    session.execute(stmt)
    return len(rows)


def ingest_asset_fundamentals(session, provider, asset: Asset) -> int:
    """Statements (stocks) + profile (stocks and ETFs). Returns rows upserted."""
    provider_symbol = asset.provider_symbol_map.get(provider.name, asset.symbol)
    rows = 0
    if asset.asset_class in STATEMENT_CLASSES:
        periods = provider.get_fundamentals(provider_symbol)
        rows = upsert_fundamentals(session, asset.id, periods, provider.name)
    profile = provider.get_profile(provider_symbol)
    if profile:
        asset.meta = {**(asset.meta or {}), "profile": profile}
        session.add(asset)
    return rows


def run_fundamentals_refresh(asset_id: int | None = None) -> int:
    """Weekly job (or on-demand for one asset). Returns ingestion_runs.id."""
    provider = get_fundamentals_provider()
    run_id = _open_run(
        "fundamentals_refresh" if asset_id is None else "fundamentals_asset",
        "stock",
        provider.name,
    )
    ok = failed = written = 0
    errors: dict[str, str] = {}
    session = SessionLocal()
    try:
        query = select(Asset).where(Asset.is_active)
        if asset_id is None:
            # stalest-first round-robin, capped to stay inside the provider day budget
            last_fetched = (
                select(
                    Fundamentals.asset_id,
                    func.max(Fundamentals.fetched_at).label("last_fetched"),
                )
                .group_by(Fundamentals.asset_id)
                .subquery()
            )
            query = (
                query.where(Asset.asset_class.in_(PROFILE_CLASSES))
                .outerjoin(last_fetched, last_fetched.c.asset_id == Asset.id)
                .order_by(last_fetched.c.last_fetched.asc().nulls_first(), Asset.id)
                .limit(ASSETS_PER_RUN)
            )
        else:
            query = query.where(Asset.id == asset_id)
        for asset in session.scalars(query).all():
            try:
                written += ingest_asset_fundamentals(session, provider, asset)
                session.commit()
                ok += 1
                log.info("fundamentals_refreshed", symbol=asset.symbol)
            except Exception as exc:  # noqa: BLE001 — budget/one-symbol issues shan't sink the run
                session.rollback()
                failed += 1
                errors[asset.symbol] = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("fundamentals_failed", symbol=asset.symbol, error=str(exc))
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
