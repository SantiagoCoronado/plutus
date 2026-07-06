"""Price-alert evaluator: fire once on an actual threshold crossing.

Runs every minute as a Celery beat task over the live quote cache
(`quote:last:<SYMBOL>`, written by the streamer). For each armed rule we compare
the previous observation (`last_price`) with the current quote:

  - `last_price IS NULL` is the re-baseline contract: it is a first observation
    (fresh rule, or re-armed / retargeted via the alerts API, which clears
    last_price). We record the price and NEVER fire on it.
  - Otherwise we fire only on a strict crossing — the previous quote sat on or
    behind the threshold and the current quote is strictly beyond it. Equality on
    the *current* side does not count as beyond, so a quote that merely touches
    the threshold never fires.

A fire flips the rule to 'triggered' (one-shot until the user re-arms), stamps
last_triggered_at, and hands off to notify.deliver. The flip happens regardless
of delivery outcome, so a flapping price can never re-fire a triggered rule.
A missing quote (expired TTL / streamer down) skips the rule without touching
last_price, so the crossing memory survives an outage.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.discovery.notify import deliver
from app.models import AlertRule, Asset
from app.quotes.publisher import read_last_quotes_sync

log = get_logger(__name__)


def should_fire(
    condition: str, threshold: Decimal, last_price: Decimal | None, current: Decimal
) -> bool:
    """Crossing-edge test — pure, so the full matrix is unit-tested without a DB.

    first observation (last_price is None) -> never fire (baseline only)
    above -> fire iff last_price <= threshold AND current > threshold
    below -> fire iff last_price >= threshold AND current < threshold
    """
    if last_price is None:
        return False
    if condition == "above":
        return last_price <= threshold and current > threshold
    if condition == "below":
        return last_price >= threshold and current < threshold
    return False


def evaluate_alerts(session: Session, redis_client=None) -> dict[str, int]:
    """Evaluate every armed rule against the latest cached quote once.

    Returns a summary {evaluated, fired, stale}. Commits at the end (deliver may
    also commit mid-loop when channels are configured); either way every
    last_price update and status flip is persisted.
    """
    if redis_client is None:
        from app.providers.registry import _shared_redis

        redis_client = _shared_redis()

    rows = session.execute(
        select(AlertRule, Asset.symbol, Asset.name)
        .join(Asset, Asset.id == AlertRule.asset_id)
        .where(AlertRule.status == "armed")
    ).all()

    symbols = [symbol for _, symbol, _ in rows]
    quotes = read_last_quotes_sync(redis_client, symbols)

    evaluated = fired = stale = 0
    for rule, symbol, name in rows:
        tick = quotes.get(symbol.upper())
        current = _tick_price(tick)
        if current is None:
            # no fresh quote — keep the crossing memory, try again next minute
            stale += 1
            continue

        evaluated += 1
        if should_fire(rule.condition, rule.threshold, rule.last_price, current):
            rule.status = "triggered"
            rule.last_triggered_at = datetime.now(UTC)
            rule.last_price = current
            fired += 1
            _notify(session, rule, symbol, name, current)
        else:
            # baseline (first observation) or no crossing — remember this side
            rule.last_price = current

    session.commit()
    log.info("alerts_evaluated", evaluated=evaluated, fired=fired, stale=stale)
    return {"evaluated": evaluated, "fired": fired, "stale": stale}


def _tick_price(tick: dict[str, Any] | None) -> Decimal | None:
    """Current price as Decimal (matches the Numeric columns), or None when the
    quote is missing or unparseable — both treated as 'no observation'."""
    if not tick or "price" not in tick:
        return None
    try:
        return Decimal(str(tick["price"]))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _notify(
    session: Session, rule: AlertRule, symbol: str, name: str | None, price: Decimal
) -> None:
    """Deliver the crossing over every configured channel. A delivery failure is
    already logged per-channel by deliver and must NOT fail the run or undo the
    status flip (which is already set on the session)."""
    threshold = _fmt_amount(rule.threshold)
    subject = f"Price alert: {symbol} {rule.condition} {threshold}"
    asset_name = name or symbol
    body = (
        f"{asset_name} ({symbol}) is now {_fmt_amount(price)}, "
        f"{rule.condition} your alert threshold of {threshold}."
    )
    meta = {
        "alert_id": rule.id,
        "asset_id": rule.asset_id,
        "symbol": symbol,
        "condition": rule.condition,
        "threshold": float(rule.threshold),
        "price": float(price),
    }
    try:
        deliver(session, "price_alert", subject, body, meta)
    except Exception as exc:  # noqa: BLE001 — delivery must never fail the evaluation
        log.warning("alert_deliver_failed", alert_id=rule.id, error=str(exc))


def _fmt_amount(value: Decimal) -> str:
    """Human-readable number: fixed-point, trailing zeros trimmed ('120000',
    '120.25', '0.5') — Numeric(20,8) otherwise renders '120000.00000000'."""
    text = f"{value:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text
