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
        "metadata": {"provider_symbols": {"coingecko": "bitcoin", "binance": "BTCUSDT"}},
    },
    {
        "symbol": "EURUSD",
        "name": "Euro / US Dollar",
        "asset_class": "forex",
        "exchange": None,
        "currency": "USD",
        "metadata": {"provider_symbols": {"twelvedata": "EUR/USD"}},
    },
    # feeds the portfolio's peso conversion (app/portfolio/fx.py reads forex closes)
    {
        "symbol": "USDMXN",
        "name": "US Dollar / Mexican Peso",
        "asset_class": "forex",
        "exchange": None,
        "currency": "MXN",
        "metadata": {"provider_symbols": {"twelvedata": "USD/MXN"}},
    },
]

# Benchmarks for relative strength (spec §5.3) — ordinary tracked assets, ingested by
# the same nightly EOD jobs. BTC (crypto benchmark) is already in SEED_ASSETS.
BENCHMARK_ASSETS: list[dict] = [
    {
        "symbol": "SPY",
        "name": "SPDR S&P 500 ETF Trust",
        "asset_class": "etf",
        "exchange": "NYSEARCA",
        "currency": "USD",
        "metadata": {"provider_symbols": {"tiingo": "SPY"}, "benchmark": True},
    },
    # DXY itself is paid-gated on Twelve Data's free tier (verified: 404) — UUP is the
    # ETF proxy for the dollar index, served by tiingo like any stock/ETF
    {
        "symbol": "UUP",
        "name": "Invesco DB US Dollar Index Bullish Fund",
        "asset_class": "etf",
        "exchange": "NYSEARCA",
        "currency": "USD",
        "metadata": {"provider_symbols": {"tiingo": "UUP"}, "benchmark": True},
    },
]


# Extra market-strip members (dashboard §9.1 + the live-quote streamer) that aren't
# already benchmarks: ETH mirrors BTC's crypto shape; QQQ is a benchmark-style ETF.
MARKET_STRIP_ASSETS: list[dict] = [
    {
        "symbol": "ETH",
        "name": "Ethereum",
        "asset_class": "crypto",
        "exchange": None,
        "currency": "USD",
        "metadata": {"provider_symbols": {"coingecko": "ethereum", "binance": "ETHUSDT"}},
    },
    {
        "symbol": "QQQ",
        "name": "Invesco QQQ Trust",
        "asset_class": "etf",
        "exchange": "NASDAQ",
        "currency": "USD",
        "metadata": {"provider_symbols": {"tiingo": "QQQ"}, "benchmark": True},
    },
]


def seed_assets() -> list[tuple[int, str]]:
    specs = SEED_ASSETS + BENCHMARK_ASSETS + MARKET_STRIP_ASSETS
    with session_scope() as session:
        for spec in specs:
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
                Asset.symbol.in_([s["symbol"] for s in specs])
            )
        ).all()
        return [(row.id, row.symbol) for row in rows]


def main(argv: list[str] | None = None) -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ingest", action="store_true", help="run EOD ingestion inline after seeding"
    )
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
