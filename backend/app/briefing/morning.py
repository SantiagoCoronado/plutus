"""Morning brief (spec phase 12): ONE notification per morning.

Instead of three separate daily sends (discovery digest 08:00, memo note 08:15,
maturity reminders 08:30), a single 08:45 message consolidates everything that
happened since the last successful brief, plus a recap of instant alerts and a
system-health line. Producers keep writing their artifacts (candidates, notes,
statuses, notification audit rows) — the brief only READS the database, so a
host that was down over 08:45 catches up the whole gap next morning: nothing is
lost, nothing repeats.

When the brief is enabled the individual daily senders suppress their delivery
(see notify.send_digest, research._notify_memos_ready, maturities._send_reminders);
real-time kinds (price alerts, task failures, watchdog) still send instantly and
are only recapped here. Disabled = exactly the old behavior.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.discovery.notify import _alert_threshold, _symbols, candidate_line, deliver
from app.health.aggregate import ingestion_health
from app.health.watchdog import BACKUP_HEARTBEAT_KEY
from app.models import (
    AlertRule,
    AppSetting,
    Asset,
    AssetNote,
    BankInvestment,
    Candidate,
    Mandate,
    Notification,
)

log = get_logger(__name__)

BRIEF_KIND = "morning_brief"
ENABLED_KEY = "morning_brief_enabled"
WINDOW_FALLBACK_H = 24
SCHEDULED_AT = "08:45"  # beat entry in worker/celery_app.py — keep in sync


def is_enabled(session: Session) -> bool:
    """Settings-UI row overrides the env default, same pattern as LLM settings."""
    row = session.get(AppSetting, ENABLED_KEY)
    if row is not None:
        return row.value.strip().lower() not in ("false", "0", "off")
    return get_settings().morning_brief_enabled


def set_enabled(session: Session, enabled: bool) -> None:
    row = session.get(AppSetting, ENABLED_KEY)
    if row is None:
        session.add(AppSetting(key=ENABLED_KEY, value=str(enabled).lower(), is_secret=False))
    else:
        row.value = str(enabled).lower()


# --------------------------------------------------------------------------- #
# composition                                                                  #
# --------------------------------------------------------------------------- #


def _window_start(session: Session, now: datetime) -> datetime:
    last = session.scalar(
        select(func.max(Notification.sent_at)).where(
            Notification.kind == BRIEF_KIND, Notification.ok.is_(True)
        )
    )
    return last or now - timedelta(hours=WINDOW_FALLBACK_H)


def _sent_today(session: Session, now: datetime) -> bool:
    """One brief per LOCAL day, however often the task fires (catch-up, retry)."""
    tz = ZoneInfo(get_settings().tz)
    last = session.scalar(
        select(func.max(Notification.sent_at)).where(
            Notification.kind == BRIEF_KIND, Notification.ok.is_(True)
        )
    )
    return last is not None and last.astimezone(tz).date() == now.astimezone(tz).date()


def _portfolio_section(session: Session) -> str | None:
    # the dashboard's own aggregates — the brief must never disagree with the UI
    from app.api.routes.dashboard import _portfolio_block, _ytd_block

    ccy = get_settings().base_currency
    today = date.today()
    block = _portfolio_block(session, today, ccy)
    if not block["value"]:
        return None
    lines = [f"Value: {block['value']:,.2f} {ccy}"]
    if block["day_pnl"] is not None:
        pct = f" ({block['day_pnl_pct'] * 100:+.2f}%)" if block["day_pnl_pct"] is not None else ""
        lines.append(f"Day P&L: {block['day_pnl']:+,.2f} {ccy}{pct}")
    ytd = _ytd_block(session, ccy)
    if ytd["twr_pct"] is not None:
        bench = (
            f" vs SPY {ytd['benchmark_return_pct'] * 100:+.2f}%"
            if ytd.get("benchmark_return_pct") is not None
            else ""
        )
        lines.append(f"YTD TWR: {ytd['twr_pct'] * 100:+.2f}%{bench}")
    return "\n".join(lines)


def _discovery_section(session: Session, window_start: datetime) -> str | None:
    mandates = {m.id: m for m in session.scalars(select(Mandate)).all()}
    candidates = session.scalars(
        select(Candidate)
        .where(Candidate.created_at >= window_start)
        .order_by(Candidate.mandate_id, Candidate.score.desc())
    ).all()
    keep = [
        c for c in candidates
        if c.mandate_id in mandates and c.score >= _alert_threshold(mandates[c.mandate_id])
    ]
    if not keep:
        return None
    symbols = _symbols(session, keep)
    blocks: list[str] = []
    by_mandate: dict[int, list[Candidate]] = {}
    for candidate in keep:
        by_mandate.setdefault(candidate.mandate_id, []).append(candidate)
    for mandate_id, group in by_mandate.items():
        lines = [f"{mandates[mandate_id].name}:"]
        lines += [
            f"  {candidate_line(symbols.get(c.asset_id, f'#{c.asset_id}'), c)}"
            for c in group[:5]
        ]
        if len(group) > 5:
            lines.append(f"  … and {len(group) - 5} more in the Inbox")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def _memo_section(session: Session, window_start: datetime) -> str | None:
    rows = session.execute(
        select(AssetNote, Asset.symbol)
        .join(Asset, Asset.id == AssetNote.asset_id)
        .where(AssetNote.source == "ai", AssetNote.created_at >= window_start)
        .order_by(AssetNote.created_at)
    ).all()
    if not rows:
        return None
    return "\n".join(
        f"{symbol}: {note.title or 'research memo'}" for note, symbol in rows
    )


def _maturity_section(session: Session) -> str | None:
    today = date.today()
    horizon = today + timedelta(days=get_settings().maturity_reminder_days)
    rows = session.scalars(
        select(BankInvestment)
        .where(
            BankInvestment.status == "active",
            BankInvestment.maturity_date.is_not(None),
            BankInvestment.maturity_date <= horizon,
            BankInvestment.maturity_date >= today,
        )
        .order_by(BankInvestment.maturity_date)
    ).all()
    if not rows:
        return None
    return "\n".join(
        f"{inv.name}: {float(inv.principal):,.2f} {inv.currency} matures "
        f"{inv.maturity_date.isoformat()}"
        f"{' (auto-renews)' if inv.auto_renew else ''}"
        for inv in rows
    )


def _alerts_section(session: Session, window_start: datetime) -> str | None:
    fired = session.scalars(
        select(Notification.subject)
        .where(Notification.kind == "price_alert", Notification.sent_at >= window_start)
        .order_by(Notification.sent_at)
    ).all()
    armed = session.scalar(
        select(func.count()).select_from(AlertRule).where(AlertRule.status == "armed")
    ) or 0
    if not fired and armed == 0:
        return None
    lines = [f"(recap — these already notified instantly)  armed now: {armed}"]
    lines += [f"  {subject}" for subject in dict.fromkeys(fired)]  # dedupe, keep order
    return "\n".join(lines) if fired else f"No alerts fired. Armed now: {armed}"


def _system_section(session: Session, redis_client, window_start: datetime, now: datetime) -> str:
    health = ingestion_health(session, redis_client)
    parts = [f"Ingestion: {health['status']}"]

    row = session.get(AppSetting, BACKUP_HEARTBEAT_KEY)
    if row is not None:
        try:
            age_h = (now - datetime.fromisoformat(row.value)).total_seconds() / 3600
            parts.append(f"Last backup: {age_h:.0f}h ago")
        except ValueError:
            parts.append("Last backup: unparseable heartbeat")
    else:
        parts.append("Last backup: none recorded")

    failures = session.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.kind == "task_failure", Notification.sent_at >= window_start)
    ) or 0
    if failures:
        parts.append(f"Task failures overnight: {failures} (see earlier notifications)")
    return " · ".join(parts)


def compose_brief(
    session: Session, redis_client, now: datetime
) -> tuple[str, str, dict, bool]:
    """(subject, body, meta, quiet) — quiet means no NEW content in the window
    (portfolio and system lines always exist; they are state, not news)."""
    window_start = _window_start(session, now)

    sections: list[tuple[str, str | None]] = [
        ("Portfolio", _portfolio_section(session)),
        ("New candidates", _discovery_section(session, window_start)),
        ("AI research memos", _memo_section(session, window_start)),
        ("Upcoming maturities", _maturity_section(session)),
        ("Price alerts", _alerts_section(session, window_start)),
        ("System", _system_section(session, redis_client, window_start, now)),
    ]
    news_titles = {"New candidates", "AI research memos", "Upcoming maturities"}
    quiet = all(text is None for title, text in sections if title in news_titles)

    tz = ZoneInfo(get_settings().tz)
    day = now.astimezone(tz).strftime("%a %b %d")
    subject = f"Plutus morning brief — {day}"

    body_parts = [f"== {title} ==\n{text}" for title, text in sections if text is not None]
    if quiet:
        body_parts.insert(0, "All quiet overnight — no new candidates, memos, or maturities.")
    body_parts.append(
        "AI-assisted summary, informational only — never investment advice."
    )
    meta = {
        "window_start": window_start.isoformat(),
        "sections": [title for title, text in sections if text is not None],
        "quiet": quiet,
    }
    return subject, "\n\n".join(body_parts), meta, quiet


def send_morning_brief(session: Session, redis_client=None, now: datetime | None = None) -> dict:
    """The 08:45 beat body. Once per local day; catch-up windows span downtime."""
    if redis_client is None:
        from app.providers.registry import _shared_redis

        redis_client = _shared_redis()
    now = now or datetime.now(UTC)

    if not is_enabled(session):
        return {"status": "disabled"}
    if _sent_today(session, now):
        return {"status": "already_sent_today"}

    subject, body, meta, quiet = compose_brief(session, redis_client, now)
    if quiet and get_settings().morning_brief_on_quiet == "skip":
        log.info("morning_brief_skipped_quiet")
        return {"status": "skipped_quiet"}

    deliver(session, BRIEF_KIND, subject, body, meta)
    log.info("morning_brief_sent", quiet=quiet, sections=meta["sections"])
    return {"status": "sent", "quiet": quiet, "sections": meta["sections"]}
