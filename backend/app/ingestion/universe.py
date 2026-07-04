"""Starter universe: ~100 liquid US large caps + top Binance-listed crypto (Phase 3).

The screener is only as interesting as the universe it scans. This module seeds a
static, curated list (index-membership APIs arrive with Phase 4 mandates) and
backfills the FULL configured history window for every active asset — unlike the
nightly incremental jobs, it re-fetches from `initial_backfill_days` ago, so it also
deepens assets that were first backfilled with a shorter horizon.

Pacing: the provider token bucket does the throttling (Tiingo ~45/hr -> ~80s/symbol
steady-state; the full stock run takes ~1.5-2.5h). Progress commits per asset, and
already-covered assets are skipped, so the command is safe to interrupt and re-run.

Usage:
    python -m app.ingestion.universe            # seed + backfill/deepen everything
    python -m app.ingestion.universe --seed-only
"""

import argparse
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import get_settings
from app.core.db import SessionLocal, session_scope
from app.core.logging import configure_logging, get_logger
from app.ingestion.eod import _close_run, _open_run, upsert_candles
from app.ingestion.normalize import candles_to_rows
from app.models import Asset, Ohlcv
from app.providers.registry import get_provider
from app.schemas.common import AssetClass, Interval

log = get_logger(__name__)

# Earliest stored bar within this many days of the horizon counts as "covered".
# Generous on purpose: IPOs younger than the horizon re-fetch once per run (1 request).
COVERAGE_SLACK_DAYS = 45

_STOCKS: list[tuple[str, str]] = [
    ("MSFT", "Microsoft Corporation"),
    ("GOOGL", "Alphabet Inc. Class A"),
    ("AMZN", "Amazon.com, Inc."),
    ("NVDA", "NVIDIA Corporation"),
    ("META", "Meta Platforms, Inc."),
    ("TSLA", "Tesla, Inc."),
    ("AVGO", "Broadcom Inc."),
    ("BRK-B", "Berkshire Hathaway Inc. Class B"),
    ("JPM", "JPMorgan Chase & Co."),
    ("LLY", "Eli Lilly and Company"),
    ("V", "Visa Inc."),
    ("UNH", "UnitedHealth Group Incorporated"),
    ("XOM", "Exxon Mobil Corporation"),
    ("MA", "Mastercard Incorporated"),
    ("JNJ", "Johnson & Johnson"),
    ("PG", "The Procter & Gamble Company"),
    ("HD", "The Home Depot, Inc."),
    ("COST", "Costco Wholesale Corporation"),
    ("ORCL", "Oracle Corporation"),
    ("ABBV", "AbbVie Inc."),
    ("KO", "The Coca-Cola Company"),
    ("BAC", "Bank of America Corporation"),
    ("CRM", "Salesforce, Inc."),
    ("MRK", "Merck & Co., Inc."),
    ("CVX", "Chevron Corporation"),
    ("NFLX", "Netflix, Inc."),
    ("AMD", "Advanced Micro Devices, Inc."),
    ("PEP", "PepsiCo, Inc."),
    ("TMO", "Thermo Fisher Scientific Inc."),
    ("WMT", "Walmart Inc."),
    ("ADBE", "Adobe Inc."),
    ("LIN", "Linde plc"),
    ("DIS", "The Walt Disney Company"),
    ("MCD", "McDonald's Corporation"),
    ("CSCO", "Cisco Systems, Inc."),
    ("ABT", "Abbott Laboratories"),
    ("WFC", "Wells Fargo & Company"),
    ("IBM", "International Business Machines Corporation"),
    ("GE", "GE Aerospace"),
    ("QCOM", "QUALCOMM Incorporated"),
    ("TXN", "Texas Instruments Incorporated"),
    ("INTU", "Intuit Inc."),
    ("CAT", "Caterpillar Inc."),
    ("VZ", "Verizon Communications Inc."),
    ("AMGN", "Amgen Inc."),
    ("ISRG", "Intuitive Surgical, Inc."),
    ("PFE", "Pfizer Inc."),
    ("NOW", "ServiceNow, Inc."),
    ("ACN", "Accenture plc"),
    ("DHR", "Danaher Corporation"),
    ("NEE", "NextEra Energy, Inc."),
    ("UNP", "Union Pacific Corporation"),
    ("CMCSA", "Comcast Corporation"),
    ("SPGI", "S&P Global Inc."),
    ("T", "AT&T Inc."),
    ("PM", "Philip Morris International Inc."),
    ("RTX", "RTX Corporation"),
    ("LOW", "Lowe's Companies, Inc."),
    ("GS", "The Goldman Sachs Group, Inc."),
    ("HON", "Honeywell International Inc."),
    ("UPS", "United Parcel Service, Inc."),
    ("BLK", "BlackRock, Inc."),
    ("AXP", "American Express Company"),
    ("BKNG", "Booking Holdings Inc."),
    ("SYK", "Stryker Corporation"),
    ("ELV", "Elevance Health, Inc."),
    ("COP", "ConocoPhillips"),
    ("MS", "Morgan Stanley"),
    ("VRTX", "Vertex Pharmaceuticals Incorporated"),
    ("TJX", "The TJX Companies, Inc."),
    ("PLD", "Prologis, Inc."),
    ("PANW", "Palo Alto Networks, Inc."),
    ("LMT", "Lockheed Martin Corporation"),
    ("C", "Citigroup Inc."),
    ("MDT", "Medtronic plc"),
    ("SCHW", "The Charles Schwab Corporation"),
    ("ADP", "Automatic Data Processing, Inc."),
    ("BMY", "Bristol-Myers Squibb Company"),
    ("DE", "Deere & Company"),
    ("GILD", "Gilead Sciences, Inc."),
    ("MU", "Micron Technology, Inc."),
    ("ADI", "Analog Devices, Inc."),
    ("MMC", "Marsh & McLennan Companies, Inc."),
    ("SBUX", "Starbucks Corporation"),
    ("BA", "The Boeing Company"),
    ("CB", "Chubb Limited"),
    ("SO", "The Southern Company"),
    ("MO", "Altria Group, Inc."),
    ("INTC", "Intel Corporation"),
    ("AMT", "American Tower Corporation"),
    ("ETN", "Eaton Corporation plc"),
    ("DUK", "Duke Energy Corporation"),
    ("NKE", "NIKE, Inc."),
    ("PGR", "The Progressive Corporation"),
    ("REGN", "Regeneron Pharmaceuticals, Inc."),
    ("BSX", "Boston Scientific Corporation"),
    ("CI", "The Cigna Group"),
    ("ZTS", "Zoetis Inc."),
    ("KLAC", "KLA Corporation"),
    ("EOG", "EOG Resources, Inc."),
    ("CME", "CME Group Inc."),
]

# (symbol, name, coingecko_id, binance_pair) — BTC is already in SEED_ASSETS
_CRYPTO: list[tuple[str, str, str, str]] = [
    ("ETH", "Ethereum", "ethereum", "ETHUSDT"),
    ("SOL", "Solana", "solana", "SOLUSDT"),
    ("BNB", "BNB", "binancecoin", "BNBUSDT"),
    ("XRP", "XRP", "ripple", "XRPUSDT"),
    ("DOGE", "Dogecoin", "dogecoin", "DOGEUSDT"),
    ("ADA", "Cardano", "cardano", "ADAUSDT"),
    ("AVAX", "Avalanche", "avalanche-2", "AVAXUSDT"),
    ("LINK", "Chainlink", "chainlink", "LINKUSDT"),
    ("DOT", "Polkadot", "polkadot", "DOTUSDT"),
]

UNIVERSE_STOCKS: list[dict] = [
    {
        "symbol": symbol,
        "name": name,
        "asset_class": "stock",
        "exchange": None,
        "currency": "USD",
        "metadata": {"provider_symbols": {"tiingo": symbol}},
    }
    for symbol, name in _STOCKS
]

UNIVERSE_CRYPTO: list[dict] = [
    {
        "symbol": symbol,
        "name": name,
        "asset_class": "crypto",
        "exchange": None,
        "currency": "USD",
        "metadata": {"provider_symbols": {"coingecko": coingecko_id, "binance": binance_pair}},
    }
    for symbol, name, coingecko_id, binance_pair in _CRYPTO
]


def seed_universe_assets() -> int:
    """Idempotent asset upsert (same pattern as seed.py). Returns number of specs."""
    specs = UNIVERSE_STOCKS + UNIVERSE_CRYPTO
    with session_scope() as session:
        for spec in specs:
            stmt = pg_insert(Asset.__table__).values(**spec)
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol", "asset_class"],
                set_={
                    "name": stmt.excluded["name"],
                    "currency": stmt.excluded["currency"],
                    "metadata": stmt.excluded["metadata"],
                    "is_active": True,
                },
            )
            session.execute(stmt)
    return len(specs)


def _is_covered(session, asset_id: int, horizon_start) -> bool:
    earliest = session.scalar(
        select(func.min(Ohlcv.ts)).where(Ohlcv.asset_id == asset_id, Ohlcv.interval == "1d")
    )
    if earliest is None:
        return False
    return earliest.date() <= horizon_start + timedelta(days=COVERAGE_SLACK_DAYS)


def backfill_asset_full(session, provider, asset: Asset) -> int:
    """Fetch the FULL history window (not incremental) and upsert. Returns rows written."""
    today = datetime.now(UTC).date()
    start = today - timedelta(days=get_settings().initial_backfill_days)
    provider_symbol = asset.provider_symbol_map.get(provider.name, asset.symbol)
    df = provider.get_ohlcv(
        provider_symbol, AssetClass(asset.asset_class), Interval.d1, start, today
    )
    return upsert_candles(session, candles_to_rows(df, asset.id, Interval.d1))


def run_universe_backfill() -> int:
    """Backfill/deepen every active asset to the configured horizon. Returns run id."""
    horizon_start = datetime.now(UTC).date() - timedelta(
        days=get_settings().initial_backfill_days
    )
    run_id = _open_run("universe_backfill", None, None)
    rows_total = ok = failed = skipped = 0
    errors: dict[str, str] = {}
    session = SessionLocal()
    try:
        assets = session.scalars(
            select(Asset).where(Asset.is_active).order_by(Asset.asset_class, Asset.id)
        ).all()
        for asset in assets:
            try:
                if _is_covered(session, asset.id, horizon_start):
                    skipped += 1
                    continue
                provider = get_provider(asset.asset_class)
                rows = backfill_asset_full(session, provider, asset)
                session.commit()
                rows_total += rows
                ok += 1
                log.info("universe_backfilled", symbol=asset.symbol, rows=rows)
            except Exception as exc:  # noqa: BLE001 — one bad symbol must not sink the run
                session.rollback()
                failed += 1
                errors[asset.symbol] = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("universe_backfill_failed", symbol=asset.symbol, error=str(exc))
    finally:
        session.close()

    if failed == 0:
        status = "success"
    elif ok == 0 and skipped == 0:
        status = "failed"
    else:
        status = "partial"
    details: dict = {"skipped_covered": skipped}
    if errors:
        details["errors"] = errors
    _close_run(run_id, status, rows_total, ok, failed, details)
    return run_id


def main(argv: list[str] | None = None) -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-only", action="store_true", help="upsert assets, skip backfill")
    args = parser.parse_args(argv)

    count = seed_universe_assets()
    print(f"universe: upserted {count} assets")
    if not args.seed_only:
        from app.models import IngestionRun

        run_id = run_universe_backfill()
        with session_scope() as session:
            run = session.get(IngestionRun, run_id)
            print(
                f"run {run.id} [{run.job_name}] {run.status}: {run.rows_written} rows, "
                f"ok={run.symbols_ok}, failed={run.symbols_failed}, details={run.details}"
            )


if __name__ == "__main__":
    main()
