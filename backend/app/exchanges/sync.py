"""Read-only Bitso → transactions sync.

Resumes from the account's ExchangeLink cursors, pulls trades/fundings/withdrawals
oldest→newest with marker pagination, and inserts idempotently on
(account_id, external_id) so re-runs and CSV imports dedupe against each other.
Commits page-by-page: a mid-sync failure leaves the cursor at the last committed
page and the run marked 'partial'. Unknown symbols are skipped and reported —
assets are never auto-created.

Normalization mirrors the `bitso` CSV preset in app/portfolio/csv_import.py:
book "btc_mxn" → symbol=BTC, currency=MXN; tid→external_id as the same raw string;
side→buy/sell; quantity=abs(major); price; fees=abs(fees_amount).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, session_scope
from app.core.logging import get_logger
from app.exchanges.base import ExchangeFunding, ExchangeTrade, ExchangeWithdrawal
from app.exchanges.bitso import PAGE_LIMIT, build_bitso_client
from app.exchanges.settings_store import get_bitso_credentials
from app.models import Account, Asset, ExchangeLink, ExchangeSyncRun, Transaction

log = get_logger(__name__)

# Fiat that lands as a cash deposit/withdrawal (no asset). Anything else is treated
# as crypto: a tracked asset → transfer_in/out, an unknown symbol → skip + report.
FIAT_CURRENCIES = frozenset(
    {"MXN", "USD", "EUR", "ARS", "BRL", "COP", "CLP", "PEN", "GBP", "CAD"}
)


def sync_bitso_account(session_or_factory, account_id: int) -> int:
    """Sync one exchange account. Accepts a live Session or a session factory
    (SessionLocal); returns the ExchangeSyncRun id."""
    if isinstance(session_or_factory, Session):
        return _run_sync(session_or_factory, account_id)
    session = session_or_factory()
    try:
        return _run_sync(session, account_id)
    finally:
        session.close()


def sync_all_bitso_accounts(session_factory=SessionLocal) -> list[int]:
    """Nightly driver: every bitso-linked exchange account. No creds → skip cleanly."""
    with session_scope() as session:
        if get_bitso_credentials(session) is None:
            log.info("sync_exchange_nightly: no bitso credentials configured — skipping")
            return []
        linked = session.scalars(
            select(ExchangeLink.account_id).where(ExchangeLink.provider == "bitso")
        ).all()
        configured = session.scalars(
            select(Account.id).where(Account.type == "exchange", Account.provider == "bitso")
        ).all()
        account_ids = sorted(set(linked) | set(configured))
    return [sync_bitso_account(session_factory, account_id) for account_id in account_ids]


# -- internals ----------------------------------------------------------------


def _run_sync(session: Session, account_id: int) -> int:
    account = session.get(Account, account_id)
    if account is None or account.type != "exchange":
        raise ValueError(f"account {account_id} is not an exchange account")

    link = _get_or_create_link(session, account_id)
    run = ExchangeSyncRun(account_id=account_id, provider="bitso", status="running")
    session.add(run)
    session.commit()  # persist the running run so it survives a later page rollback
    run_id = run.id

    creds = get_bitso_credentials(session)
    if creds is None:
        _close(session, run, link, "failed", 0, 0, {"error": "no Bitso API credentials configured"})
        return run_id

    client = build_bitso_client(*creds)
    assets = _asset_lookup(session)
    created = skipped = 0
    unknown: set[str] = set()
    pages_committed = 0

    try:
        c, s, pages_committed = _sync_trades(session, account_id, link, client, assets, unknown)
        created += c
        skipped += s
        c, s, p = _sync_fundings(session, account_id, link, client, assets, unknown)
        created += c
        skipped += s
        pages_committed += p
        c, s, p = _sync_withdrawals(session, account_id, link, client, assets, unknown)
        created += c
        skipped += s
        pages_committed += p
    except Exception as exc:  # noqa: BLE001 — a mid-sync failure must not lose committed pages
        session.rollback()
        status = "partial" if pages_committed > 0 else "failed"
        details: dict[str, Any] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        if unknown:
            details["skipped_unknown_symbols"] = sorted(unknown)
        log.warning("bitso sync failed", account_id=account_id, error=str(exc))
        _close(session, run, link, status, created, skipped, details)
        return run_id

    details = {"skipped_unknown_symbols": sorted(unknown)} if unknown else {}
    _close(session, run, link, "success", created, skipped, details)
    return run_id


def _get_or_create_link(session: Session, account_id: int) -> ExchangeLink:
    link = session.scalar(select(ExchangeLink).where(ExchangeLink.account_id == account_id))
    if link is None:
        link = ExchangeLink(account_id=account_id, provider="bitso")
        session.add(link)
        session.flush()
    return link


def _asset_lookup(session: Session) -> dict[str, list[Asset]]:
    lookup: dict[str, list[Asset]] = {}
    for asset in session.scalars(select(Asset)).all():
        lookup.setdefault(asset.symbol.upper(), []).append(asset)
    return lookup


def _resolve_asset(assets: dict[str, list[Asset]], symbol: str) -> Asset | None:
    matches = assets.get(symbol.upper(), [])
    if not matches:
        return None
    crypto = [a for a in matches if a.asset_class == "crypto"]
    if len(crypto) == 1:
        return crypto[0]
    if len(matches) == 1:
        return matches[0]
    return None  # ambiguous across classes — treat as unresolved, skip + report


def _insert(session: Session, record: dict) -> bool:
    """Idempotent insert on (account_id, external_id). True when a row was created."""
    stmt = (
        pg_insert(Transaction.__table__)
        .values(**record)
        .on_conflict_do_nothing(
            index_elements=["account_id", "external_id"],
            index_where=text("external_id IS NOT NULL"),
        )
        .returning(Transaction.__table__.c.id)
    )
    return session.execute(stmt).first() is not None


def _sync_trades(
    session: Session,
    account_id: int,
    link: ExchangeLink,
    client,
    assets: dict[str, list[Asset]],
    unknown: set[str],
) -> tuple[int, int, int]:
    created = skipped = pages = 0
    marker = link.last_trade_tid
    while True:
        page: list[ExchangeTrade] = client.fetch_trades(since_tid=marker)
        if not page:
            break
        for trade in page:
            record = _trade_record(account_id, trade, assets, unknown)
            if record is None:
                continue
            if _insert(session, record):
                created += 1
            else:
                skipped += 1
        marker = page[-1].tid
        link.last_trade_tid = marker
        session.commit()
        pages += 1
        if len(page) < PAGE_LIMIT:
            break
    return created, skipped, pages


def _sync_fundings(
    session: Session,
    account_id: int,
    link: ExchangeLink,
    client,
    assets: dict[str, list[Asset]],
    unknown: set[str],
) -> tuple[int, int, int]:
    created = skipped = pages = 0
    marker = link.last_funding_id
    while True:
        page: list[ExchangeFunding] = client.fetch_fundings(since_id=marker)
        if not page:
            break
        for funding in page:
            if funding.status != "complete":
                continue
            record = _transfer_record(
                account_id, funding.currency, funding.amount, funding.fid,
                "deposit", "transfer_in", funding.created_at, funding.method, assets, unknown,
            )
            if record is None:
                continue
            if _insert(session, record):
                created += 1
            else:
                skipped += 1
        marker = page[-1].fid
        link.last_funding_id = marker
        session.commit()
        pages += 1
        if len(page) < PAGE_LIMIT:
            break
    return created, skipped, pages


def _sync_withdrawals(
    session: Session,
    account_id: int,
    link: ExchangeLink,
    client,
    assets: dict[str, list[Asset]],
    unknown: set[str],
) -> tuple[int, int, int]:
    created = skipped = pages = 0
    marker = link.last_withdrawal_id
    while True:
        page: list[ExchangeWithdrawal] = client.fetch_withdrawals(since_id=marker)
        if not page:
            break
        for withdrawal in page:
            if withdrawal.status != "complete":
                continue
            record = _transfer_record(
                account_id, withdrawal.currency, withdrawal.amount, withdrawal.wid,
                "withdrawal", "transfer_out", withdrawal.created_at, withdrawal.method,
                assets, unknown,
            )
            if record is None:
                continue
            if _insert(session, record):
                created += 1
            else:
                skipped += 1
        marker = page[-1].wid
        link.last_withdrawal_id = marker
        session.commit()
        pages += 1
        if len(page) < PAGE_LIMIT:
            break
    return created, skipped, pages


def _trade_record(
    account_id: int,
    trade: ExchangeTrade,
    assets: dict[str, list[Asset]],
    unknown: set[str],
) -> dict | None:
    base, _, quote = trade.book.partition("_")
    symbol = base.upper()
    currency = quote.upper()
    asset = _resolve_asset(assets, symbol)
    if asset is None:
        unknown.add(symbol)
        return None
    return {
        "account_id": account_id,
        "asset_id": asset.id,
        "type": trade.side,
        "ts": trade.created_at,
        "quantity": abs(trade.major),
        "price": trade.price,
        "fees": abs(trade.fees_amount),
        "currency": currency,
        "note": None,
        "external_id": trade.tid,
        "lot_links": None,
    }


def _transfer_record(
    account_id: int,
    currency: str,
    amount: float,
    external_id: str,
    fiat_type: str,
    crypto_type: str,
    ts: datetime,
    method: str | None,
    assets: dict[str, list[Asset]],
    unknown: set[str],
) -> dict | None:
    currency = currency.upper()
    if currency in FIAT_CURRENCIES:
        # cash movement — money-only, no asset
        return {
            "account_id": account_id,
            "asset_id": None,
            "type": fiat_type,
            "ts": ts,
            "quantity": abs(amount),
            "price": None,
            "fees": 0,
            "currency": currency,
            "note": method,
            "external_id": external_id,
            "lot_links": None,
        }
    asset = _resolve_asset(assets, currency)
    if asset is None:
        unknown.add(currency)
        return None
    return {
        "account_id": account_id,
        "asset_id": asset.id,
        "type": crypto_type,
        "ts": ts,
        "quantity": abs(amount),
        "price": None,
        "fees": 0,
        "currency": currency,
        "note": method,
        "external_id": external_id,
        "lot_links": None,
    }


def _close(
    session: Session,
    run: ExchangeSyncRun,
    link: ExchangeLink,
    status: str,
    created: int,
    skipped: int,
    details: dict,
) -> None:
    run = session.get(ExchangeSyncRun, run.id)
    run.status = status
    run.finished_at = datetime.now(UTC)
    run.trades_created = created
    run.trades_skipped = skipped
    run.details = details or None
    link = session.get(ExchangeLink, link.id)
    link.last_status = status
    link.last_synced_at = datetime.now(UTC)
    session.commit()
