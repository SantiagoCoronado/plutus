from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.logging import get_logger
from app.models import Asset, Ohlcv
from app.providers.registry import configured_providers
from app.schemas.asset import AssetCreate, AssetOut, SearchResponse, SearchResultItem
from app.schemas.common import Interval
from app.schemas.ohlcv import Candle, OhlcvResponse

log = get_logger(__name__)
router = APIRouter(prefix="/assets", tags=["assets"])

MAX_CANDLES = 10_000


@router.get("/search", response_model=SearchResponse)
def search_assets(
    q: str = Query(min_length=1, max_length=50),
    db: Session = Depends(get_db),
):
    pattern = f"%{q}%"
    local = db.scalars(
        select(Asset)
        .where(or_(Asset.symbol.ilike(pattern), Asset.name.ilike(pattern)))
        .order_by(Asset.symbol)
        .limit(20)
    ).all()
    tracked_keys = {(a.symbol.upper(), a.asset_class) for a in local}
    results = [
        SearchResultItem(
            symbol=a.symbol,
            name=a.name,
            asset_class=a.asset_class,
            exchange=a.exchange,
            currency=a.currency,
            tracked=True,
            asset_id=a.id,
        )
        for a in local
    ]

    # Provider suggestions degrade gracefully: short rate-limit budget, any failure -> local only
    for provider in configured_providers():
        try:
            for info in provider.search_symbols(q)[:10]:
                if (info.symbol.upper(), info.asset_class.value) in tracked_keys:
                    continue
                results.append(SearchResultItem.from_symbol_info(info))
        except Exception as exc:  # noqa: BLE001 — search must never 500 on provider issues
            log.warning("provider_search_failed", provider=provider.name, error=str(exc))

    return SearchResponse(query=q, results=results[:40])


@router.post("", response_model=AssetOut, status_code=201)
def track_asset(body: AssetCreate, db: Session = Depends(get_db)):
    """Track a new asset (the write half of search-then-track); enqueues a history backfill."""
    stmt = pg_insert(Asset.__table__).values(
        symbol=body.symbol.upper(),
        name=body.name,
        asset_class=body.asset_class.value,
        exchange=body.exchange,
        currency=body.currency,
        metadata=body.meta,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol", "asset_class"],
        set_={
            "name": stmt.excluded["name"],
            "exchange": stmt.excluded["exchange"],
            "currency": stmt.excluded["currency"],
            "metadata": stmt.excluded["metadata"],
            "is_active": True,
        },
    ).returning(Asset.__table__.c.id)
    asset_id = db.execute(stmt).scalar_one()
    db.commit()

    try:
        from worker.tasks import backfill_asset

        backfill_asset.delay(asset_id)
    except Exception as exc:  # noqa: BLE001 — tracking succeeds even if the broker is down
        log.warning("backfill_enqueue_failed", asset_id=asset_id, error=str(exc))

    return db.get(Asset, asset_id)


@router.get("/{asset_id}", response_model=AssetOut)
def get_asset(asset_id: int, db: Session = Depends(get_db)):
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="asset not found")
    return asset


@router.get("/{asset_id}/ohlcv", response_model=OhlcvResponse)
def get_ohlcv(
    asset_id: int,
    interval: Interval = Interval.d1,
    start: date | None = None,
    end: date | None = None,
    limit: int = Query(default=MAX_CANDLES, ge=1, le=MAX_CANDLES),
    db: Session = Depends(get_db),
):
    if db.get(Asset, asset_id) is None:
        raise HTTPException(status_code=404, detail="asset not found")
    query = (
        select(Ohlcv)
        .where(Ohlcv.asset_id == asset_id, Ohlcv.interval == interval.value)
        .order_by(Ohlcv.ts)
        .limit(limit)
    )
    if start is not None:
        query = query.where(Ohlcv.ts >= start)
    if end is not None:
        query = query.where(Ohlcv.ts <= end)
    bars = db.scalars(query).all()
    return OhlcvResponse(
        asset_id=asset_id,
        interval=interval,
        candles=[Candle.model_validate(b, from_attributes=True) for b in bars],
    )
