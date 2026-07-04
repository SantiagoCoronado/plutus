"""Scan lifecycle (queued -> running -> done|failed) and the beat dispatcher.

Mirrors app/backtest/runner.py: failures become row state, never Celery errors.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.discovery.schedule import is_due
from app.models import Mandate, Scan

log = get_logger(__name__)


def execute_scan(scan_id: int) -> int:
    session = SessionLocal()
    try:
        scan = session.get(Scan, scan_id)
        if scan is None:
            log.warning("scan_missing", scan_id=scan_id)
            return scan_id
        scan.status = "running"
        scan.started_at = datetime.now(UTC)
        session.commit()

        mandate = None
        outcome = None
        try:
            from app.discovery.engine import run_mandate_scan

            mandate = session.get(Mandate, scan.mandate_id)
            if mandate is None:
                raise ValueError("mandate no longer exists")
            outcome = run_mandate_scan(session, mandate, scan_id=scan.id)
            scan.stats = outcome["stats"]
            scan.status = "done"
            log.info("scan_done", scan_id=scan_id, mandate_id=scan.mandate_id, **outcome["stats"])
        except Exception as exc:  # noqa: BLE001 — failures become row state, not task errors
            session.rollback()
            scan = session.get(Scan, scan_id)
            scan.status = "failed"
            scan.error = f"{type(exc).__name__}: {exc}"[:1000]
            log.warning("scan_failed", scan_id=scan_id, error=str(exc))
        scan.finished_at = datetime.now(UTC)
        session.commit()

        # alerts run after the commit so they reference persisted candidates;
        # a notify failure is logged in `notifications`, never fails the scan
        if scan.status == "done" and outcome is not None and mandate is not None:
            try:
                from app.discovery.notify import notify_scan

                notify_scan(session, mandate, scan, outcome["candidates"])
            except Exception as exc:  # noqa: BLE001
                log.warning("scan_notify_failed", scan_id=scan_id, error=str(exc))
        return scan_id
    finally:
        session.close()


def dispatch_due_mandates() -> list[int]:
    """Beat entry (every 5 minutes): enqueue a scan for each active mandate whose
    schedule has come due. last_run_at advances at enqueue time — even if the
    enqueue fails, the miss is a visible failed scan row, not a retry storm."""
    session = SessionLocal()
    dispatched: list[int] = []
    try:
        now = datetime.now(UTC)
        mandates = session.scalars(select(Mandate).where(Mandate.active.is_(True))).all()
        for mandate in mandates:
            in_flight = session.scalar(
                select(func.count())
                .select_from(Scan)
                .where(Scan.mandate_id == mandate.id, Scan.status.in_(("queued", "running")))
            )
            if in_flight:
                continue
            anchor = mandate.last_run_at or mandate.created_at
            try:
                if not is_due(mandate.schedule, anchor):
                    continue
            except ValueError as exc:
                log.warning("mandate_bad_schedule", mandate_id=mandate.id, error=str(exc))
                continue

            scan = Scan(mandate_id=mandate.id)
            mandate.last_run_at = now
            session.add(scan)
            session.commit()

            from worker.tasks import run_scan

            try:
                run_scan.delay(scan.id)
            except Exception as exc:  # noqa: BLE001 — broker down: visible failed row
                scan.status = "failed"
                scan.error = f"could not enqueue: {exc}"[:1000]
                scan.finished_at = datetime.now(UTC)
                session.commit()
                log.warning("scan_enqueue_failed", scan_id=scan.id, error=str(exc))
                continue
            dispatched.append(scan.id)
            log.info("scan_dispatched", scan_id=scan.id, mandate_id=mandate.id)
        return dispatched
    finally:
        session.close()
