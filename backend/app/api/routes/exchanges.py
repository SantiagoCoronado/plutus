from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import get_settings
from app.exchanges.settings_store import (
    EXCHANGE_EDITABLE_KEYS,
    get_bitso_credentials,
    masked_bitso_keys,
    set_exchange_setting,
)
from app.exchanges.sync import reset_cursors, unresolved_skip_count
from app.llm.crypto import FernetKeyMissing
from app.models import Account, ExchangeLink, ExchangeSyncRun
from app.providers.base import ProviderAuthError, ProviderError
from app.schemas.exchanges import (
    BitsoKeysIn,
    BitsoTestOut,
    ExchangeAccountOut,
    ExchangeRunOut,
    ExchangeStatusOut,
)

router = APIRouter(prefix="/exchanges", tags=["exchanges"])


def _latest_run(db: Session, account_id: int) -> ExchangeSyncRun | None:
    return db.scalar(
        select(ExchangeSyncRun)
        .where(ExchangeSyncRun.account_id == account_id)
        .order_by(ExchangeSyncRun.started_at.desc(), ExchangeSyncRun.id.desc())
        .limit(1)
    )


@router.get("/status", response_model=ExchangeStatusOut)
def exchange_status(db: Session = Depends(get_db)):
    env = get_settings()
    keys = masked_bitso_keys(db)
    links = {
        link.account_id: link
        for link in db.scalars(select(ExchangeLink)).all()
    }
    accounts = db.scalars(
        select(Account).where(Account.type == "exchange").order_by(Account.name)
    ).all()

    out_accounts: list[ExchangeAccountOut] = []
    for account in accounts:
        link = links.get(account.id)
        run = _latest_run(db, account.id)
        out_accounts.append(
            ExchangeAccountOut(
                account_id=account.id,
                name=account.name,
                provider=account.provider or (link.provider if link else None),
                last_synced_at=link.last_synced_at if link else None,
                last_status=link.last_status if link else None,
                unresolved_skips=unresolved_skip_count(db, account.id),
                last_run=(
                    ExchangeRunOut(
                        status=run.status,
                        trades_created=run.trades_created,
                        trades_skipped=run.trades_skipped,
                        finished_at=run.finished_at,
                        details=run.details,
                    )
                    if run
                    else None
                ),
            )
        )

    return ExchangeStatusOut(
        configured=get_bitso_credentials(db) is not None,
        keys=keys,
        fernet_ready=bool(env.fernet_key),
        accounts=out_accounts,
    )


@router.put("/bitso/keys", response_model=ExchangeStatusOut)
def update_bitso_keys(body: BitsoKeysIn, db: Session = Depends(get_db)):
    updates: dict[str, str] = {}
    if body.api_key is not None:
        updates["bitso_api_key"] = body.api_key.strip()
    if body.api_secret is not None:
        updates["bitso_api_secret"] = body.api_secret.strip()
    try:
        for key, value in updates.items():
            assert key in EXCHANGE_EDITABLE_KEYS  # closed set, defensive
            set_exchange_setting(db, key, value)
    except FernetKeyMissing as exc:
        raise HTTPException(
            status_code=422,
            detail={"errors": [{"path": "keys", "error": str(exc)}]},
        ) from exc
    db.commit()
    return exchange_status(db)


@router.post("/bitso/test", response_model=BitsoTestOut)
def test_bitso(db: Session = Depends(get_db)):
    creds = get_bitso_credentials(db)
    if creds is None:
        raise HTTPException(status_code=422, detail="Bitso API key and secret are not configured")
    from app.exchanges.bitso import build_bitso_client

    try:
        client = build_bitso_client(*creds)
        balances = client.fetch_balances()
        return BitsoTestOut(ok=True, currencies=len(balances))
    except ProviderAuthError:
        return BitsoTestOut(
            ok=False, error="Bitso rejected the credentials — check the API key and secret."
        )
    except ProviderError as exc:
        return BitsoTestOut(ok=False, error=f"Could not reach Bitso: {exc}")


@router.post("/{account_id}/sync", status_code=202)
def sync_account(account_id: int, db: Session = Depends(get_db)):
    _require_exchange_account(db, account_id)
    return _enqueue_sync(account_id)


@router.post("/{account_id}/resync", status_code=202)
def resync_account(account_id: int, db: Session = Depends(get_db)):
    """Rewalk the account's full history: cursors reset to the beginning and the
    sync runs in repair mode, so synced trade rows are refreshed from provider
    data and previously skipped items get another chance. Dedup on external_id
    guarantees the rewalk creates zero duplicates."""
    _require_exchange_account(db, account_id)
    reset_cursors(db, account_id)
    return _enqueue_sync(account_id, repair=True)


def _require_exchange_account(db: Session, account_id: int) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    if account.type != "exchange":
        raise HTTPException(status_code=422, detail="account is not an exchange account")
    return account


def _enqueue_sync(account_id: int, repair: bool = False):
    try:
        from worker.tasks import sync_exchange

        if repair:
            result = sync_exchange.delay(account_id, True)
        else:
            result = sync_exchange.delay(account_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503, detail=f"could not enqueue sync (is redis/worker up?): {exc}"
        ) from exc
    return {"task_id": result.id, "status": "queued"}
