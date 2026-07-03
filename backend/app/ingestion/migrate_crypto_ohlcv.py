"""One-off: wipe synthetic crypto candles and re-backfill real OHLCV.

The Phase 1 CoinGecko adapter synthesized H/L from closes; those bars poison
ATR/ADX/Stochastic/52w metrics and — because ingestion windows are incremental —
would never be revisited after switching PROVIDER_CRYPTO to binance. So: delete
crypto 1d rows, then run a fresh full backfill inline.

Not an Alembic migration on purpose: this depends on env/provider config and network;
schema migrations must not.

Usage: python -m app.ingestion.migrate_crypto_ohlcv
"""

import sqlalchemy as sa

from app.core.db import session_scope
from app.core.logging import configure_logging
from app.ingestion.eod import _close_run, _open_run, run_eod_ingestion
from app.models import Asset, IngestionRun, Ohlcv


def main() -> None:
    configure_logging()
    run_id = _open_run("crypto_provider_migration", "crypto", None)
    with session_scope() as session:
        deleted = session.execute(
            sa.delete(Ohlcv).where(
                Ohlcv.asset_id.in_(sa.select(Asset.id).where(Asset.asset_class == "crypto")),
                Ohlcv.interval == "1d",
            )
        ).rowcount
    _close_run(run_id, "success", 0, 0, 0, {"deleted_rows": deleted})
    print(f"deleted {deleted} synthetic crypto rows")

    backfill_run_id = run_eod_ingestion("crypto")
    with session_scope() as session:
        run = session.get(IngestionRun, backfill_run_id)
        print(
            f"backfill run {run.id} [{run.job_name}] {run.status}: {run.rows_written} rows, "
            f"ok={run.symbols_ok}, failed={run.symbols_failed}"
            + (f", details={run.details}" if run.details else "")
        )


if __name__ == "__main__":
    main()
