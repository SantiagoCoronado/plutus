"""Backtest lifecycle: queued -> running -> done|failed. Runs inside the worker.

Never raises to Celery — a failed run is a row with status='failed' and an error
message the UI can show, not a dead-lettered task.
"""

from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.models import Backtest

log = get_logger(__name__)


def artifact_path_for(backtest_id: int) -> Path:
    root = Path(get_settings().artifacts_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root / f"backtest_{backtest_id}.html"


def execute_backtest(backtest_id: int) -> int:
    session = SessionLocal()
    try:
        backtest = session.get(Backtest, backtest_id)
        if backtest is None:
            log.warning("backtest_missing", backtest_id=backtest_id)
            return backtest_id
        backtest.status = "running"
        backtest.started_at = datetime.now(UTC)
        session.commit()

        try:
            if backtest.kind == "screen":
                from app.backtest.screen import run_screen_backtest

                result = run_screen_backtest(session, backtest.params)
            else:
                from app.backtest.strategy import run_strategy_backtest

                result = run_strategy_backtest(
                    session, backtest.params, artifact_path_for(backtest_id)
                )

            backtest.stats = result["stats"]
            backtest.equity_curve = result["equity_curve"]
            backtest.trade_list = result["trade_list"]
            backtest.artifact_path = result.get("artifact_path")
            backtest.status = "done"
            log.info("backtest_done", backtest_id=backtest_id, kind=backtest.kind)
        except Exception as exc:  # noqa: BLE001 — failures become row state, not task errors
            session.rollback()
            backtest = session.get(Backtest, backtest_id)
            backtest.status = "failed"
            backtest.error = f"{type(exc).__name__}: {exc}"[:1000]
            log.warning("backtest_failed", backtest_id=backtest_id, error=str(exc))
        backtest.finished_at = datetime.now(UTC)
        session.commit()
        return backtest_id
    finally:
        session.close()
