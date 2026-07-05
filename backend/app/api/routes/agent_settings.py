from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import get_settings
from app.llm.budget import tokens_used_today
from app.llm.crypto import FernetKeyMissing
from app.llm.settings_store import (
    EDITABLE_KEYS,
    PROVIDER_KEY_FIELD,
    get_llm_settings,
    mask_secret,
    set_setting,
)
from app.models import AgentToolCall
from app.schemas.agent_settings import (
    AgentActionOut,
    LLMSettingsIn,
    LLMSettingsOut,
    SidecarStatusOut,
    TestConnectionIn,
    TestConnectionOut,
    UsageOut,
)

router = APIRouter(prefix="/agent", tags=["agent"])

_KEY_NAMES = ("anthropic_api_key", "openai_api_key", "google_api_key", "openrouter_api_key")


def _sidecar_status(url: str) -> SidecarStatusOut:
    try:
        resp = httpx.get(f"{url}/health", timeout=2.0)
        data = resp.json() if resp.status_code == 200 else {}
        return SidecarStatusOut(
            url=url,
            reachable=resp.status_code == 200,
            auth_ok=data.get("auth") == "oauth",
        )
    except (httpx.HTTPError, ValueError):
        return SidecarStatusOut(url=url, reachable=False, auth_ok=False)


@router.get("/settings", response_model=LLMSettingsOut)
def get_agent_settings(db: Session = Depends(get_db)):
    cfg = get_llm_settings(db)
    env = get_settings()
    return LLMSettingsOut(
        provider=cfg.provider,
        model=cfg.model,
        keys={name: mask_secret(getattr(cfg, name)) for name in _KEY_NAMES},
        sidecar=_sidecar_status(cfg.claude_sidecar_url),
        daily_token_budget=env.agent_daily_token_budget,
        fernet_configured=bool(env.fernet_key),
    )


@router.put("/settings", response_model=LLMSettingsOut)
def update_agent_settings(body: LLMSettingsIn, db: Session = Depends(get_db)):
    updates: dict[str, str] = {}
    if body.provider is not None:
        updates["llm_provider"] = body.provider
    if body.model is not None:
        updates["llm_model"] = body.model
    for name, value in (body.keys or {}).items():
        if name not in EDITABLE_KEYS or not EDITABLE_KEYS[name]:
            raise HTTPException(
                status_code=422,
                detail={"errors": [{"path": f"keys.{name}", "error": "unknown key name"}]},
            )
        updates[name] = value.strip()
    try:
        for key, value in updates.items():
            set_setting(db, key, value)
    except FernetKeyMissing as exc:
        raise HTTPException(
            status_code=422,
            detail={"errors": [{"path": "keys", "error": str(exc)}]},
        ) from exc
    db.commit()
    return get_agent_settings(db)


@router.post("/settings/test", response_model=TestConnectionOut)
def test_connection(body: TestConnectionIn, db: Session = Depends(get_db)):
    cfg = get_llm_settings(db)
    provider = body.provider or cfg.provider

    if provider == "claude-subscription":
        status = _sidecar_status(cfg.claude_sidecar_url)
        if not status.reachable:
            detail = f"sidecar not reachable at {status.url} — is the agent-sidecar service up?"
        elif not status.auth_ok:
            detail = "sidecar is up but has no CLAUDE_CODE_OAUTH_TOKEN — run `claude setup-token`"
        else:
            detail = "sidecar healthy, subscription auth present"
        return TestConnectionOut(ok=status.reachable and status.auth_ok,
                                 provider=provider, detail=detail)

    key_field = PROVIDER_KEY_FIELD.get(provider)
    if key_field and not cfg.api_key_for(provider):
        return TestConnectionOut(
            ok=False, provider=provider, detail=f"no API key configured ({key_field})"
        )
    return _test_api_provider(cfg, provider)


def _test_api_provider(cfg, provider: str) -> TestConnectionOut:
    # replaced with a real 1-token ping when the provider layer lands (M3)
    return TestConnectionOut(
        ok=False, provider=provider, detail="provider ping not implemented yet"
    )


@router.get("/usage", response_model=UsageOut)
def get_usage(db: Session = Depends(get_db)):
    env = get_settings()
    used = tokens_used_today(db)
    today = datetime.now(ZoneInfo(env.tz)).date().isoformat()
    return UsageOut(
        date=today,
        tokens_used=used,
        daily_token_budget=env.agent_daily_token_budget,
        remaining=max(0, env.agent_daily_token_budget - used),
    )


@router.get("/actions", response_model=list[AgentActionOut])
def list_actions(
    db: Session = Depends(get_db),
    source: str | None = Query(default=None, pattern="^(app|task|mcp)$"),
    tier: str | None = Query(default=None, pattern="^(read|write)$"),
    limit: int = Query(default=50, ge=1, le=200),
):
    stmt = select(AgentToolCall).order_by(AgentToolCall.created_at.desc()).limit(limit)
    if source is not None:
        stmt = stmt.where(AgentToolCall.source == source)
    if tier is not None:
        stmt = stmt.where(AgentToolCall.tier == tier)
    rows = db.scalars(stmt).all()
    return [
        AgentActionOut(
            id=row.id,
            conversation_id=row.conversation_id,
            source=row.source,
            tier=row.tier,
            name=row.name,
            arguments=row.arguments,
            status=row.status,
            result_summary=row.result_summary,
            error=row.error,
            created_at=row.created_at,
        )
        for row in rows
    ]
