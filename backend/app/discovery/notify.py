"""Alert channels for scan results (filled in by the alerts milestone)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Candidate, Mandate, Scan


def notify_scan(
    session: Session, mandate: Mandate, scan: Scan, candidates: list[Candidate]
) -> None:
    return None


def send_digest() -> int:
    return 0
