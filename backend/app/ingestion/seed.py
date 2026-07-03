"""Deterministic bootstrap: upsert the three Phase 1 gate assets.

Usage:
    python -m app.ingestion.seed            # upsert assets, print ids
    python -m app.ingestion.seed --ingest   # + run full EOD ingestion inline (no worker needed)
"""

import argparse

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import session_scope
from app.core.logging import configure_logging
from app.models import Asset, IngestionRun

SEED_ASSETS: list[dict] = [
    {
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "asset_class": "stock",
        "exchange": "NASDAQ",
        "currency": "USD",
        "metadata": {"provider_symbols": {"tiingo": "AAPL"}},
    },
    {
        "symbol": "BTC",
        "name": "Bitcoin",
        "asset_class": "crypto",
        "exchange": None,
        "currency": "USD",
        "metadata": {"provider_symbols": {"coingecko": "bitcoin"}},
    },
    {
        "symbol": "EURUSD",
        "name": "Euro / US Dollar",
        "asset_class": "forex",
        "exchange": None,
        "currency": "USD",
        "metadata": {"provider_symbols": {"twelvedata": "EUR/USD"}},
    },
]


def seed_assets() -> list[tuple[int, str]]:
    with session_scope() as session:
        for spec in SEED_ASSETS:
            # insert on the table, not the ORM class: the "metadata" column name would
            # otherwise resolve against Base.metadata in values()
            stmt = pg_insert(Asset.__table__).values(**spec)
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol", "asset_class"],
                set_={
                    # bracket access: .metadata on `excluded` collides with Table.metadata
                    "name": stmt.excluded["name"],
                    "exchange": stmt.excluded["exchange"],
                    "currency": stmt.excluded["currency"],
                    "metadata": stmt.excluded["metadata"],
                    "is_active": True,
                },
            )
            session.execute(stmt)
        rows = session.execute(
            select(Asset.id, Asset.symbol).where(
                Asset.symbol.in_([s["symbol"] for s in SEED_ASSETS])
            )
        ).all()
        return [(row.id, row.symbol) for row in rows]


def main(argv: list[str] | None = None) -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ingest", action="store_true", help="run EOD ingestion inline after seeding")
    args = parser.parse_args(argv)

    for asset_id, symbol in seed_assets():
        print(f"seeded asset {symbol} (id={asset_id})")

    if args.ingest:
        from app.ingestion.eod import run_eod_all

        run_ids = run_eod_all()
        with session_scope() as session:
            for run_id in run_ids:
                run = session.get(IngestionRun, run_id)
                print(
                    f"run {run.id} [{run.job_name}] {run.status}: "
                    f"{run.rows_written} rows, ok={run.symbols_ok}, failed={run.symbols_failed}"
                    + (f", details={run.details}" if run.details else "")
                )


if __name__ == "__main__":
    main()
