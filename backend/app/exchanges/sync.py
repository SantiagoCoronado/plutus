"""Read-only Bitso → transactions sync.

Resumes from the account's ExchangeLink cursors, pulls trades/fundings/withdrawals
oldest→newest with marker pagination, and inserts idempotently on
(account_id, external_id) so re-runs and CSV imports dedupe against each other.
Commits page-by-page: a mid-sync failure leaves the cursor at the last committed
page and the run marked 'partial'. Unknown symbols are skipped and reported —
assets are never auto-created.

Nothing is ever lost behind a cursor:
- Funding/withdrawal cursors only advance past rows in a TERMINAL status; a
  pending row freezes the persisted cursor (the walk still continues forward in
  memory), so the row is re-fetched on later syncs until it completes or fails.
- Every item that cannot land yet is upserted into exchange_sync_skips with its
  normalized payload. `unknown_symbol` skips retry from that payload at the start
  of each sync (the asset may be tracked by then); `pending_status` skips resolve
  when the re-walk finds the row terminal.
- reset_cursors() rewalks history from the beginning (dedup keeps it duplicate-
  free); repair=True additionally overwrites synced trade rows in place from the
  fresh provider data (fee/quantity fixes back-propagate).

Normalization mirrors the `bitso` CSV preset in app/portfolio/csv_import.py:
book "btc_mxn" → symbol=BTC, currency=MXN; tid→external_id as the same raw string;
side→buy/sell; quantity=abs(major); price. Fees are carried in the trade's quote
currency: a fee charged in the received (major) asset on a buy is netted out of
quantity with its quote value carried in `fees`, so basis, cash, and holdings all
stay exact (total cost still equals the minor amount paid).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, session_scope
from app.core.logging import get_logger
from app.exchanges.base import ExchangeFunding, ExchangeTrade, ExchangeWithdrawal
from app.exchanges.bitso import PAGE_LIMIT, build_bitso_client
from app.exchanges.settings_store import get_bitso_credentials
from app.models import (
    Account,
    Asset,
    ExchangeLink,
    ExchangeSyncRun,
    ExchangeSyncSkip,
    Transaction,
)

log = get_logger(__name__)

# Fiat that lands as a cash deposit/withdrawal (no asset). Anything else is treated
# as crypto: a tracked asset → transfer_in/out, an unknown symbol → skip + report.
FIAT_CURRENCIES = frozenset(
    {"MXN", "USD", "EUR", "ARS", "BRL", "COP", "CLP", "PEN", "GBP", "CAD"}
)

# Statuses that can never change again. Anything else (pending, in_progress, …)
# freezes the persisted cursor so the row is re-checked on the next sync.
TERMINAL_STATUSES = frozenset({"complete", "failed", "cancelled"})


def sync_bitso_account(session_or_factory, account_id: int, *, repair: bool = False) -> int:
    """Sync one exchange account. Accepts a live Session or a session factory
    (SessionLocal); returns the ExchangeSyncRun id. repair=True overwrites
    already-synced trade rows from fresh provider data (use with reset_cursors)."""
    if isinstance(session_or_factory, Session):
        return _run_sync(session_or_factory, account_id, repair=repair)
    session = session_or_factory()
    try:
        return _run_sync(session, account_id, repair=repair)
    finally:
        session.close()


def reset_cursors(session: Session, account_id: int) -> None:
    """Forget where the last sync stopped so the next run rewalks from the
    beginning. Idempotent inserts guarantee the rewalk creates zero duplicates."""
    link = _get_or_create_link(session, account_id)
    link.last_trade_tid = None
    link.last_funding_id = None
    link.last_withdrawal_id = None
    session.commit()


def unresolved_skip_count(session: Session, account_id: int) -> int:
    return session.scalar(
        select(func.count())
        .select_from(ExchangeSyncSkip)
        .where(
            ExchangeSyncSkip.account_id == account_id,
            ExchangeSyncSkip.resolved_at.is_(None),
        )
    ) or 0


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


def _run_sync(session: Session, account_id: int, *, repair: bool = False) -> int:
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
        created += _retry_skips(session, account_id, assets, unknown)
        c, s, pages_committed = _sync_trades(
            session, account_id, link, client, assets, unknown, repair=repair
        )
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

    details: dict[str, Any] = {}
    if unknown:
        details["skipped_unknown_symbols"] = sorted(unknown)
    unresolved = unresolved_skip_count(session, account_id)
    if unresolved:
        details["unresolved_skips"] = unresolved
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


def _insert(session: Session, record: dict, *, repair: bool = False) -> bool:
    """Idempotent insert on (account_id, external_id). True when a row was created.
    repair=True overwrites the synced row's provider-owned fields in place instead
    of skipping it, so normalization fixes back-propagate on a cursor-reset rewalk."""
    stmt = pg_insert(Transaction.__table__).values(**record)
    if repair:
        stmt = stmt.on_conflict_do_update(
            index_elements=["account_id", "external_id"],
            index_where=text("external_id IS NOT NULL"),
            set_={
                key: getattr(stmt.excluded, key)
                for key in ("type", "ts", "quantity", "price", "fees", "currency", "note")
            },
        )
    else:
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["account_id", "external_id"],
            index_where=text("external_id IS NOT NULL"),
        )
    stmt = stmt.returning(Transaction.__table__.c.id)
    return session.execute(stmt).first() is not None


def _record_skip(
    session: Session,
    account_id: int,
    stream: str,
    external_id: str,
    reason: str,
    payload: dict[str, Any],
) -> None:
    """Upsert one unresolved skip; re-seeing the same item refreshes its payload."""
    stmt = (
        pg_insert(ExchangeSyncSkip.__table__)
        .values(
            account_id=account_id,
            stream=stream,
            external_id=external_id,
            reason=reason,
            payload=payload,
        )
        .on_conflict_do_update(
            constraint="uq_exchange_sync_skips_item",
            set_={"reason": reason, "payload": payload, "last_seen_at": func.now()},
        )
    )
    session.execute(stmt)


def _pending_skip_ids(session: Session, account_id: int, stream: str) -> set[str]:
    return set(
        session.scalars(
            select(ExchangeSyncSkip.external_id).where(
                ExchangeSyncSkip.account_id == account_id,
                ExchangeSyncSkip.stream == stream,
                ExchangeSyncSkip.resolved_at.is_(None),
            )
        )
    )


def _resolve_skip(session: Session, account_id: int, stream: str, external_id: str) -> None:
    session.execute(
        update(ExchangeSyncSkip.__table__)
        .where(
            ExchangeSyncSkip.account_id == account_id,
            ExchangeSyncSkip.stream == stream,
            ExchangeSyncSkip.external_id == external_id,
            ExchangeSyncSkip.resolved_at.is_(None),
        )
        .values(resolved_at=func.now())
    )


def _retry_skips(
    session: Session,
    account_id: int,
    assets: dict[str, list[Asset]],
    unknown: set[str],
) -> int:
    """Re-attempt unknown_symbol skips from their stored payloads — the asset may
    be tracked by now. pending_status skips are NOT retried here: their payload is
    stale by definition and the frozen cursor re-walks them from the API instead."""
    skips = session.scalars(
        select(ExchangeSyncSkip).where(
            ExchangeSyncSkip.account_id == account_id,
            ExchangeSyncSkip.reason == "unknown_symbol",
            ExchangeSyncSkip.resolved_at.is_(None),
        )
    ).all()
    created = 0
    for skip in skips:
        record = _record_from_skip(account_id, skip.stream, skip.payload, assets, unknown)
        if record is None:
            continue
        if _insert(session, record):
            created += 1
        _resolve_skip(session, account_id, skip.stream, skip.external_id)
    if skips:
        session.commit()
    return created


def _record_from_skip(
    account_id: int,
    stream: str,
    payload: dict[str, Any],
    assets: dict[str, list[Asset]],
    unknown: set[str],
) -> dict | None:
    data = dict(payload)
    data["created_at"] = datetime.fromisoformat(data["created_at"])
    if stream == "trade":
        return _trade_record(account_id, ExchangeTrade(**data), assets, unknown)
    fiat_type, crypto_type = (
        ("deposit", "transfer_in") if stream == "funding" else ("withdrawal", "transfer_out")
    )
    external_id = data["fid"] if stream == "funding" else data["wid"]
    return _transfer_record(
        account_id, data["currency"], data["amount"], external_id,
        fiat_type, crypto_type, data["created_at"], data.get("method"), assets, unknown,
    )


def _trade_payload(trade: ExchangeTrade) -> dict[str, Any]:
    return {
        "tid": trade.tid, "book": trade.book, "side": trade.side,
        "major": trade.major, "minor": trade.minor, "price": trade.price,
        "fees_amount": trade.fees_amount, "fees_currency": trade.fees_currency,
        "created_at": trade.created_at.isoformat(),
    }


def _funding_payload(item: ExchangeFunding) -> dict[str, Any]:
    return {
        "fid": item.fid, "currency": item.currency, "amount": item.amount,
        "status": item.status, "created_at": item.created_at.isoformat(),
        "method": item.method,
    }


def _withdrawal_payload(item: ExchangeWithdrawal) -> dict[str, Any]:
    return {
        "wid": item.wid, "currency": item.currency, "amount": item.amount,
        "status": item.status, "created_at": item.created_at.isoformat(),
        "method": item.method,
    }


def _sync_trades(
    session: Session,
    account_id: int,
    link: ExchangeLink,
    client,
    assets: dict[str, list[Asset]],
    unknown: set[str],
    *,
    repair: bool = False,
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
                # unknown symbol: the cursor moves on (the symbol may never be
                # tracked) but the payload is kept so a retry can land it later
                _record_skip(
                    session, account_id, "trade", trade.tid,
                    "unknown_symbol", _trade_payload(trade),
                )
                continue
            if _insert(session, record, repair=repair):
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
    return _sync_transfers(
        session, account_id, link, assets, unknown,
        stream="funding",
        fetch=client.fetch_fundings,
        item_id=lambda item: item.fid,
        payload=_funding_payload,
        types=("deposit", "transfer_in"),
        get_cursor=lambda: link.last_funding_id,
        set_cursor=lambda value: setattr(link, "last_funding_id", value),
    )


def _sync_withdrawals(
    session: Session,
    account_id: int,
    link: ExchangeLink,
    client,
    assets: dict[str, list[Asset]],
    unknown: set[str],
) -> tuple[int, int, int]:
    return _sync_transfers(
        session, account_id, link, assets, unknown,
        stream="withdrawal",
        fetch=client.fetch_withdrawals,
        item_id=lambda item: item.wid,
        payload=_withdrawal_payload,
        types=("withdrawal", "transfer_out"),
        get_cursor=lambda: link.last_withdrawal_id,
        set_cursor=lambda value: setattr(link, "last_withdrawal_id", value),
    )


def _sync_transfers(
    session: Session,
    account_id: int,
    link: ExchangeLink,
    assets: dict[str, list[Asset]],
    unknown: set[str],
    *,
    stream: str,
    fetch,
    item_id,
    payload,
    types: tuple[str, str],
    get_cursor,
    set_cursor,
) -> tuple[int, int, int]:
    """Shared funding/withdrawal walk. The persisted cursor only advances through
    the leading run of TERMINAL rows: the first pending row freezes it (so the next
    sync re-fetches from there), while the in-memory walk keeps going so nothing
    newer waits on a stuck deposit. Inserts are idempotent, so the eventual re-walk
    over already-landed rows just counts dedup skips."""
    fiat_type, crypto_type = types
    created = skipped = pages = 0
    marker = get_cursor()
    persist = marker
    halted = False
    pending_ids = _pending_skip_ids(session, account_id, stream)
    while True:
        page = fetch(since_id=marker)
        if not page:
            break
        for item in page:
            external_id = item_id(item)
            if item.status not in TERMINAL_STATUSES:
                if not halted:
                    halted = True
                    log.info(
                        "bitso sync: non-terminal item freezes cursor",
                        stream=stream, external_id=external_id, status=item.status,
                    )
                _record_skip(
                    session, account_id, stream, external_id,
                    "pending_status", payload(item),
                )
                pending_ids.add(external_id)
                continue
            if external_id in pending_ids:
                _resolve_skip(session, account_id, stream, external_id)
                pending_ids.discard(external_id)
            if item.status == "complete":
                record = _transfer_record(
                    account_id, item.currency, item.amount, external_id,
                    fiat_type, crypto_type, item.created_at, item.method, assets, unknown,
                )
                if record is None:
                    _record_skip(
                        session, account_id, stream, external_id,
                        "unknown_symbol", payload(item),
                    )
                elif _insert(session, record):
                    created += 1
                else:
                    skipped += 1
            if not halted:
                persist = external_id
        marker = item_id(page[-1])
        set_cursor(persist)
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

    quantity = abs(trade.major)
    fee = abs(trade.fees_amount)
    fee_currency = (trade.fees_currency or quote).upper()
    note = None
    if fee > 0 and fee_currency == symbol:
        # Bitso charges buy-side fees in the received (major) asset. Net the fee
        # out of quantity and carry its quote value in `fees`: holdings match what
        # actually arrived and total cost still equals the minor amount paid
        # (q_net·p + fee·p = major·p). Sells with a major-denominated fee (not
        # observed on Bitso) get the same quote conversion without the netting.
        if trade.side == "buy" and fee < quantity:
            quantity -= fee
            note = f"fee {fee:g} {symbol} netted from quantity"
        else:
            note = f"fee {fee:g} {symbol} carried at execution price"
        fee = fee * trade.price
    elif fee > 0 and fee_currency != currency:
        # a third currency we can't convert — keep the trade, surface the oddity
        note = f"fee {fee:g} {fee_currency} not converted"
        fee = 0.0
    return {
        "account_id": account_id,
        "asset_id": asset.id,
        "type": trade.side,
        "ts": trade.created_at,
        "quantity": quantity,
        "price": trade.price,
        "fees": fee,
        "currency": currency,
        "note": note,
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
