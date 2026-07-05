"""Daily token budget (spec §13.4): one SUM over agent_messages covers every
surface (chat, research tasks, translations), because all of them persist
their usage there. "Today" is the app timezone's calendar day, not UTC.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings


class BudgetExceeded(Exception):
    def __init__(self, used: int, budget: int) -> None:
        self.used = used
        self.budget = budget
        super().__init__(
            f"daily token budget reached ({used:,} of {budget:,} tokens used today)"
        )


def _day_start_utc() -> datetime:
    zone = ZoneInfo(get_settings().tz)
    local_midnight = datetime.combine(datetime.now(zone).date(), time.min, tzinfo=zone)
    return local_midnight.astimezone(ZoneInfo("UTC"))


def tokens_used_today(session: Session) -> int:
    from app.models import AgentMessage

    total = session.execute(
        select(
            func.coalesce(func.sum(AgentMessage.input_tokens), 0)
            + func.coalesce(func.sum(AgentMessage.output_tokens), 0)
        ).where(AgentMessage.created_at >= _day_start_utc())
    ).scalar_one()
    return int(total)


def ensure_budget(session: Session) -> None:
    budget = get_settings().agent_daily_token_budget
    used = tokens_used_today(session)
    if used >= budget:
        raise BudgetExceeded(used, budget)
