"""Alert channels: email (SMTP) and Telegram, both env-configured and optional.

One message per scan, never per candidate. Every send attempt is logged to
`notifications`; unconfigured channels are skipped without a row. Channel
failures never fail the scan that triggered them.
"""

from __future__ import annotations

import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models import Asset, Candidate, Mandate, Notification, Scan

log = get_logger(__name__)

SMTP_TIMEOUT_S = 15.0
TELEGRAM_TIMEOUT_S = 10.0
# first digest ever covers the last day
DIGEST_FALLBACK_HOURS = 24


def configured_channels() -> list[str]:
    settings = get_settings()
    channels = []
    if settings.smtp_host and settings.alert_email_to:
        channels.append("email")
    if settings.telegram_bot_token and settings.telegram_chat_id:
        channels.append("telegram")
    return channels


def send_email(subject: str, body: str) -> None:
    settings = get_settings()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.alert_email_from or settings.smtp_user or "plutus@localhost"
    message["To"] = settings.alert_email_to
    message.set_content(body)

    if settings.smtp_port == 465:  # implicit TLS
        with smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_S
        ) as smtp:
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_pass)
            smtp.send_message(message)
        return
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_S) as smtp:
        try:
            smtp.starttls()
        except smtplib.SMTPNotSupportedError:
            pass  # local/dev relays without TLS
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_pass)
        smtp.send_message(message)


def send_telegram(subject: str, body: str) -> None:
    settings = get_settings()
    response = httpx.post(
        f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
        json={"chat_id": settings.telegram_chat_id, "text": f"{subject}\n\n{body}"},
        timeout=TELEGRAM_TIMEOUT_S,
    )
    response.raise_for_status()


SENDERS = {"email": send_email, "telegram": send_telegram}


def deliver(
    session: Session, kind: str, subject: str, body: str, meta: dict[str, Any]
) -> list[dict[str, Any]]:
    """Attempt every configured channel independently; log one row per attempt."""
    channels = configured_channels()
    if not channels:
        log.warning("alerts_skipped_not_configured", kind=kind, subject=subject)
        return []
    results = []
    for channel in channels:
        ok, error = True, None
        try:
            SENDERS[channel](subject, body)
        except Exception as exc:  # noqa: BLE001 — a channel failure is a logged row
            ok, error = False, f"{type(exc).__name__}: {exc}"[:500]
            log.warning("alert_send_failed", channel=channel, error=error)
        session.add(
            Notification(
                channel=channel, kind=kind, subject=subject, body=body, meta=meta, ok=ok,
                error=error,
            )
        )
        results.append({"channel": channel, "ok": ok, "error": error})
    session.commit()
    return results


def candidate_line(symbol: str, candidate: Candidate) -> str:
    triggered = [item["label"] for item in candidate.signals if item.get("triggered")]
    line = f"{symbol} — score {candidate.score:.0f} — {', '.join(triggered)}"
    history = _history_summary(candidate)
    if history:
        line += f" — {history}"
    return line


def _history_summary(candidate: Candidate) -> str | None:
    checks = (candidate.context or {}).get("history_check") or {}
    for item in candidate.signals:
        check = checks.get(item["key"])
        if check and check.get("fwd", {}).get("20d"):
            fwd = check["fwd"]["20d"]
            return (
                f"after {check['n_triggers']} past signals: "
                f"{fwd['median']:+.1%} median 20-day move, {fwd['win_rate']:.0%} win rate"
            )
    return None


def _alert_threshold(mandate: Mandate) -> float:
    return (
        mandate.notify_min_score if mandate.notify_min_score is not None else mandate.min_score
    )


def _symbols(session: Session, candidates: list[Candidate]) -> dict[int, str]:
    asset_ids = {candidate.asset_id for candidate in candidates}
    rows = session.execute(
        select(Asset.id, Asset.symbol).where(Asset.id.in_(asset_ids))
    ).all()
    return dict(rows)


def notify_scan(
    session: Session, mandate: Mandate, scan: Scan, candidates: list[Candidate]
) -> None:
    """Instant alert at the end of a successful scan — one message for the batch."""
    if mandate.notify != "instant" or not candidates:
        return
    threshold = _alert_threshold(mandate)
    qualifying = sorted(
        (c for c in candidates if c.score >= threshold), key=lambda c: c.score, reverse=True
    )
    if not qualifying:
        return
    symbols = _symbols(session, qualifying)
    count = len(qualifying)
    plural = "s" if count != 1 else ""
    subject = f'Plutus: {count} new idea{plural} from "{mandate.name}"'
    body = "\n".join(candidate_line(symbols[c.asset_id], c) for c in qualifying)
    deliver(
        session,
        "instant",
        subject,
        body,
        {
            "mandate_id": mandate.id,
            "scan_id": scan.id,
            "candidate_ids": [c.id for c in qualifying],
        },
    )


def send_digest() -> int:
    """Daily summary of candidates created since the last successful digest, grouped
    by mandate. If every channel failed, the window does not advance and tomorrow's
    digest re-covers the same candidates."""
    from app.core.db import SessionLocal

    session = SessionLocal()
    try:
        window_start = session.scalar(
            select(func.max(Notification.sent_at)).where(
                Notification.kind == "digest", Notification.ok.is_(True)
            )
        )
        if window_start is None:
            window_start = datetime.now(UTC) - timedelta(hours=DIGEST_FALLBACK_HOURS)

        rows = session.execute(
            select(Candidate, Mandate)
            .join(Mandate, Mandate.id == Candidate.mandate_id)
            .where(Mandate.notify == "digest", Candidate.created_at > window_start)
            .order_by(Mandate.name, Candidate.score.desc())
        ).all()
        qualifying = [
            (candidate, mandate)
            for candidate, mandate in rows
            if candidate.score >= _alert_threshold(mandate)
        ]
        if not qualifying:
            return 0

        symbols = _symbols(session, [candidate for candidate, _ in qualifying])
        sections: dict[str, list[str]] = {}
        for candidate, mandate in qualifying:
            sections.setdefault(mandate.name, []).append(
                f"  {candidate_line(symbols[candidate.asset_id], candidate)}"
            )
        count = len(qualifying)
        plural = "s" if count != 1 else ""
        subject = f"Plutus daily summary: {count} new idea{plural}"
        body = "\n\n".join(f"{name}\n" + "\n".join(lines) for name, lines in sections.items())
        deliver(
            session,
            "digest",
            subject,
            body,
            {"candidate_ids": [candidate.id for candidate, _ in qualifying]},
        )
        return count
    finally:
        session.close()


def send_test_alert(session: Session) -> list[dict[str, Any]]:
    return deliver(
        session, "test", "Plutus test alert", "Alert channels are working.", {}
    )
