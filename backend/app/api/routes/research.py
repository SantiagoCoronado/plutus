from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.data import load_ohlcv_frame
from app.analysis.indicators import INDICATORS, compute_series
from app.analysis.resample import BUCKET_WIDTHS, resample_frame
from app.api.deps import get_db
from app.core.logging import get_logger
from app.models import Asset, AssetMetrics, AssetNote, Fundamentals, NewsItem
from app.schemas.common import Interval
from app.schemas.fundamentals import FundamentalsOut
from app.schemas.metrics import AssetMetricsOut
from app.schemas.news import NewsItemOut
from app.schemas.note import NoteCreate, NoteOut, NoteUpdate

log = get_logger(__name__)
router = APIRouter(prefix="/assets", tags=["research"])


def _get_asset_or_404(db: Session, asset_id: int) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="asset not found")
    return asset


@router.get("/{asset_id}/metrics", response_model=AssetMetricsOut)
def get_metrics(asset_id: int, db: Session = Depends(get_db)):
    _get_asset_or_404(db, asset_id)
    metrics = db.get(AssetMetrics, asset_id)
    if metrics is None:
        raise HTTPException(
            status_code=404,
            detail="metrics not computed yet — run the metrics refresh (POST /ingestion/run)",
        )
    return metrics


@router.get("/{asset_id}/indicators")
def get_indicators(
    asset_id: int,
    keys: str = Query(description="comma-separated indicator keys, e.g. sma_20,rsi_14,macd"),
    interval: Interval = Interval.d1,
    start: date | None = None,
    end: date | None = None,
    db: Session = Depends(get_db),
):
    """Server-side indicator series, LWC-shaped ({time: unix_s, value}).

    Computed on the FULL history then sliced to [start, end] so warm-up windows
    (sma_200, MACD, ADX) stay correct near the requested range's left edge.
    """
    _get_asset_or_404(db, asset_id)
    requested = [k.strip() for k in keys.split(",") if k.strip()]
    unknown = [k for k in requested if k not in INDICATORS]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail={"unknown_keys": unknown, "valid_keys": sorted(INDICATORS)},
        )

    if interval == Interval.d1:
        df = load_ohlcv_frame(db, asset_id, lookback_days=0)
    elif interval.value in BUCKET_WIDTHS:
        df = resample_frame(db, asset_id, interval.value)
    else:
        raise HTTPException(status_code=422, detail=f"interval {interval} not chartable")

    computed = compute_series(df, requested)
    if start is not None:
        start_dt = datetime(start.year, start.month, start.day, tzinfo=UTC)
        computed = computed[computed.index >= start_dt]
    if end is not None:
        end_dt = datetime(end.year, end.month, end.day, tzinfo=UTC)
        computed = computed[computed.index <= end_dt]

    series: dict = {}
    for key in requested:
        spec = INDICATORS[key]
        cols = [c for c in spec.output_columns() if c in computed.columns]
        if not cols:
            continue  # skipped: volume-less asset or not enough bars
        if len(spec.output_columns()) == 1:
            series[key] = _points(computed[cols[0]])
        else:
            series[key] = {col: _points(computed[col]) for col in cols}
    return {"asset_id": asset_id, "interval": interval.value, "series": series}


def _points(series) -> list[dict]:
    cleaned = series.dropna()
    return [
        {"time": int(ts.timestamp()), "value": float(value)} for ts, value in cleaned.items()
    ]


@router.get("/{asset_id}/fundamentals", response_model=list[FundamentalsOut])
def get_fundamentals(
    asset_id: int,
    period: str = Query(default="annual", pattern="^(annual|quarter|ttm)$"),
    limit: int = Query(default=6, ge=1, le=20),
    db: Session = Depends(get_db),
):
    _get_asset_or_404(db, asset_id)
    return db.scalars(
        select(Fundamentals)
        .where(Fundamentals.asset_id == asset_id, Fundamentals.period == period)
        .order_by(Fundamentals.report_date.desc())
        .limit(limit)
    ).all()


@router.post("/{asset_id}/fundamentals/refresh", status_code=202)
def refresh_fundamentals(asset_id: int, db: Session = Depends(get_db)):
    asset = _get_asset_or_404(db, asset_id)
    if asset.asset_class not in ("stock", "etf"):
        raise HTTPException(status_code=422, detail="fundamentals apply to stock/etf assets only")
    try:
        from worker.tasks import refresh_fundamentals_asset

        result = refresh_fundamentals_asset.delay(asset_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"could not enqueue: {exc}") from exc
    return {"task_id": result.id, "status": "queued"}


@router.get("/{asset_id}/news", response_model=list[NewsItemOut])
def get_news(
    asset_id: int,
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    asset = _get_asset_or_404(db, asset_id)
    horizon = datetime.now(UTC) - timedelta(days=days)
    return db.scalars(
        select(NewsItem)
        .where(NewsItem.tickers.contains([asset.symbol]), NewsItem.ts >= horizon)
        .order_by(NewsItem.ts.desc())
        .limit(limit)
    ).all()


# --- notes -----------------------------------------------------------------


@router.get("/{asset_id}/notes", response_model=list[NoteOut])
def list_notes(asset_id: int, db: Session = Depends(get_db)):
    _get_asset_or_404(db, asset_id)
    return db.scalars(
        select(AssetNote)
        .where(AssetNote.asset_id == asset_id)
        .order_by(AssetNote.updated_at.desc())
    ).all()


@router.post("/{asset_id}/notes", response_model=NoteOut, status_code=201)
def create_note(asset_id: int, body: NoteCreate, db: Session = Depends(get_db)):
    _get_asset_or_404(db, asset_id)
    note = AssetNote(asset_id=asset_id, title=body.title, body_md=body.body_md, source="user")
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


@router.patch("/{asset_id}/notes/{note_id}", response_model=NoteOut)
def update_note(asset_id: int, note_id: int, body: NoteUpdate, db: Session = Depends(get_db)):
    note = db.get(AssetNote, note_id)
    if note is None or note.asset_id != asset_id:
        raise HTTPException(status_code=404, detail="note not found")
    if body.title is not None:
        note.title = body.title
    if body.body_md is not None:
        note.body_md = body.body_md
    note.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(note)
    return note


@router.delete("/{asset_id}/notes/{note_id}", status_code=204)
def delete_note(asset_id: int, note_id: int, db: Session = Depends(get_db)):
    note = db.get(AssetNote, note_id)
    if note is None or note.asset_id != asset_id:
        raise HTTPException(status_code=404, detail="note not found")
    db.delete(note)
    db.commit()
