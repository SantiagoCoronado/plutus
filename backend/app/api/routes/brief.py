from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.briefing.morning import SCHEDULED_AT, compose_brief, is_enabled, set_enabled
from app.core.config import get_settings
from app.discovery.notify import configured_channels, deliver

router = APIRouter(prefix="/brief", tags=["brief"])


class BriefSettingsOut(BaseModel):
    enabled: bool
    scheduled_at: str
    on_quiet: str
    channels: list[str]


class BriefSettingsIn(BaseModel):
    enabled: bool


class BriefTestOut(BaseModel):
    ok: bool
    subject: str
    sections: list[str]
    channels: list[str]
    error: str | None = None


@router.get("", response_model=BriefSettingsOut)
def brief_settings(db: Session = Depends(get_db)):
    return BriefSettingsOut(
        enabled=is_enabled(db),
        scheduled_at=SCHEDULED_AT,
        on_quiet=get_settings().morning_brief_on_quiet,
        channels=configured_channels(),
    )


@router.put("", response_model=BriefSettingsOut)
def update_brief_settings(body: BriefSettingsIn, db: Session = Depends(get_db)):
    set_enabled(db, body.enabled)
    db.commit()
    return brief_settings(db)


@router.post("/test", response_model=BriefTestOut)
def send_test_brief(db: Session = Depends(get_db)):
    """Compose the real brief and send it NOW with kind='test' — the daily
    window is untouched, so tomorrow's 08:45 brief still covers its full gap."""
    from app.providers.registry import _shared_redis

    channels = configured_channels()
    subject, body, meta, _quiet = compose_brief(db, _shared_redis(), datetime.now(UTC))
    if not channels:
        return BriefTestOut(
            ok=False,
            subject=subject,
            sections=meta["sections"],
            channels=[],
            error="no alert channels configured — set SMTP_* or TELEGRAM_* in .env",
        )
    results = deliver(db, "test", f"[test] {subject}", body, meta)
    ok = any(r.get("ok") for r in results) if results else False
    return BriefTestOut(
        ok=ok,
        subject=subject,
        sections=meta["sections"],
        channels=channels,
        error=None if ok else "delivery failed on every channel — see notifications log",
    )
