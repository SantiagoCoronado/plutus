from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import Asset, AssetMetrics, Watchlist, WatchlistItem
from app.schemas.watchlist import (
    WatchlistCreate,
    WatchlistItemAdd,
    WatchlistItemOut,
    WatchlistOut,
)

router = APIRouter(prefix="/watchlists", tags=["watchlists"])


def _items_by_watchlist(db: Session) -> dict[int, list[WatchlistItemOut]]:
    rows = db.execute(
        select(
            WatchlistItem.watchlist_id,
            WatchlistItem.asset_id,
            WatchlistItem.added_at,
            Asset.symbol,
            Asset.name,
            Asset.asset_class,
            AssetMetrics.close,
            AssetMetrics.return_1d,
        )
        .join(Asset, Asset.id == WatchlistItem.asset_id)
        .outerjoin(AssetMetrics, AssetMetrics.asset_id == WatchlistItem.asset_id)
        .order_by(Asset.symbol)
    ).all()
    grouped: dict[int, list[WatchlistItemOut]] = {}
    for row in rows:
        grouped.setdefault(row.watchlist_id, []).append(
            WatchlistItemOut(
                asset_id=row.asset_id,
                symbol=row.symbol,
                name=row.name,
                asset_class=row.asset_class,
                added_at=row.added_at,
                close=row.close,
                return_1d=row.return_1d,
            )
        )
    return grouped


@router.get("", response_model=list[WatchlistOut])
def list_watchlists(db: Session = Depends(get_db)):
    watchlists = db.scalars(select(Watchlist).order_by(Watchlist.id)).all()
    items = _items_by_watchlist(db)
    return [
        WatchlistOut(
            id=w.id, name=w.name, created_at=w.created_at, items=items.get(w.id, [])
        )
        for w in watchlists
    ]


@router.post("", response_model=WatchlistOut, status_code=201)
def create_watchlist(body: WatchlistCreate, db: Session = Depends(get_db)):
    watchlist = Watchlist(name=body.name)
    db.add(watchlist)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="watchlist name already exists") from exc
    db.refresh(watchlist)
    return WatchlistOut(id=watchlist.id, name=watchlist.name, created_at=watchlist.created_at)


@router.patch("/{watchlist_id}", response_model=WatchlistOut)
def rename_watchlist(watchlist_id: int, body: WatchlistCreate, db: Session = Depends(get_db)):
    watchlist = db.get(Watchlist, watchlist_id)
    if watchlist is None:
        raise HTTPException(status_code=404, detail="watchlist not found")
    watchlist.name = body.name
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="watchlist name already exists") from exc
    return WatchlistOut(
        id=watchlist.id,
        name=watchlist.name,
        created_at=watchlist.created_at,
        items=_items_by_watchlist(db).get(watchlist.id, []),
    )


@router.delete("/{watchlist_id}", status_code=204)
def delete_watchlist(watchlist_id: int, db: Session = Depends(get_db)):
    watchlist = db.get(Watchlist, watchlist_id)
    if watchlist is None:
        raise HTTPException(status_code=404, detail="watchlist not found")
    db.delete(watchlist)
    db.commit()


@router.post("/{watchlist_id}/items", status_code=201)
def add_item(watchlist_id: int, body: WatchlistItemAdd, db: Session = Depends(get_db)):
    if db.get(Watchlist, watchlist_id) is None:
        raise HTTPException(status_code=404, detail="watchlist not found")
    if db.get(Asset, body.asset_id) is None:
        raise HTTPException(status_code=404, detail="asset not found")
    stmt = (
        pg_insert(WatchlistItem.__table__)
        .values(watchlist_id=watchlist_id, asset_id=body.asset_id)
        .on_conflict_do_nothing(index_elements=["watchlist_id", "asset_id"])
    )
    db.execute(stmt)
    db.commit()
    return {"watchlist_id": watchlist_id, "asset_id": body.asset_id, "status": "added"}


@router.delete("/{watchlist_id}/items/{asset_id}", status_code=204)
def remove_item(watchlist_id: int, asset_id: int, db: Session = Depends(get_db)):
    item = db.get(WatchlistItem, (watchlist_id, asset_id))
    if item is None:
        raise HTTPException(status_code=404, detail="watchlist item not found")
    db.delete(item)
    db.commit()
