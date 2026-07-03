from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.ingestion.eod import _close_run, _open_run
from app.models import Asset, NewsItem
from app.providers.registry import get_news_provider
from app.schemas.news import NewsItemIn

log = get_logger(__name__)

# Finnhub company-news covers listed companies; crypto/forex news is a later adapter
NEWS_CLASSES = ("stock", "etf")
PULL_WINDOW_DAYS = 2  # overlap catches late/edited items; md5(url) dedup absorbs repeats

# merge tickers on conflict so a story shared by several symbols accumulates them
_TICKER_MERGE = sa.text("ARRAY(SELECT DISTINCT UNNEST(news_items.tickers || excluded.tickers))")


def upsert_news(session, items: list[NewsItemIn], ticker: str) -> int:
    if not items:
        return 0
    seen: dict[str, NewsItemIn] = {}
    for item in items:  # in-batch dedup — same URL twice in one statement would error
        seen.setdefault(item.url, item)
    rows = [
        {
            "ts": item.ts,
            "source": item.source,
            "headline": item.headline,
            "url": item.url,
            "tickers": [ticker],
        }
        for item in seen.values()
    ]
    stmt = pg_insert(NewsItem.__table__).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[sa.text("md5(url)")],
        set_={"tickers": _TICKER_MERGE},
    )
    session.execute(stmt)
    return len(rows)


def run_news_pull() -> int:
    """15-minute pull over all active stock/ETF assets. Returns ingestion_runs.id."""
    provider = get_news_provider()
    run_id = _open_run("news_pull", "stock", provider.name)
    ok = failed = written = 0
    errors: dict[str, str] = {}
    end = datetime.now(UTC).date()
    start = end - timedelta(days=PULL_WINDOW_DAYS)

    session = SessionLocal()
    try:
        assets = session.scalars(
            select(Asset)
            .where(Asset.is_active, Asset.asset_class.in_(NEWS_CLASSES))
            .order_by(Asset.id)
        ).all()
        for asset in assets:
            try:
                items = provider.get_company_news(asset.symbol, start, end)
                written += upsert_news(session, items, asset.symbol)
                session.commit()
                ok += 1
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                failed += 1
                errors[asset.symbol] = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("news_pull_failed", symbol=asset.symbol, error=str(exc))
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
