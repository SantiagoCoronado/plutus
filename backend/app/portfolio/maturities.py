"""Daily bank-investment maintenance: flip matured fixed terms (capitalizing
auto-renewals) and send maturity reminders through the alert channels."""

from __future__ import annotations

from datetime import date, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import session_scope
from app.discovery.notify import deliver
from app.models import Account, BankInvestment, BankInvestmentTerm, Notification
from app.portfolio.interest import Terms, projected_maturity_value

log = structlog.get_logger()


def run_maturity_check() -> int:
    """Returns the number of reminders sent (0 also when no channel is set)."""
    with session_scope() as session:
        _roll_matured(session)
        return _send_reminders(session)


def _terms(investment: BankInvestment) -> Terms:
    return Terms(
        kind=investment.kind,
        principal=float(investment.principal),
        annual_rate=float(investment.annual_rate),
        rate_tiers=investment.rate_tiers,
        cap_amount=float(investment.cap_amount) if investment.cap_amount is not None else None,
        day_count=investment.day_count,
        compounding=investment.compounding,
        start_date=investment.start_date,
        maturity_date=investment.maturity_date,
    )


def _roll_matured(session: Session) -> None:
    today = date.today()
    matured = session.scalars(
        select(BankInvestment).where(
            BankInvestment.status == "active",
            BankInvestment.kind == "fixed_term",
            BankInvestment.maturity_date <= today,
        )
    ).all()
    for investment in matured:
        if investment.auto_renew:
            value = projected_maturity_value(_terms(investment))
            note = (
                f"auto-renewed on {investment.maturity_date}: principal "
                f"{float(investment.principal):.2f} -> {value:.2f}"
            )
            renewed_on = investment.maturity_date
            _close_term(session, investment, renewed_on)
            # the parent row keeps mirroring the *current* term (readers of
            # principal/start_date stay correct); history lives in the terms
            investment.principal = round(value, 8)
            investment.start_date = renewed_on
            investment.maturity_date = investment.start_date + timedelta(
                days=investment.term_days
            )
            session.add(
                BankInvestmentTerm(
                    investment_id=investment.id,
                    start_date=renewed_on,
                    end_date=None,
                    principal=investment.principal,
                    annual_rate=investment.annual_rate,
                    rate_tiers=investment.rate_tiers,
                    cap_amount=investment.cap_amount,
                )
            )
            investment.note = f"{investment.note}\n{note}" if investment.note else note
            log.info(
                "bank_investment_renewed",
                investment_id=investment.id,
                new_principal=float(investment.principal),
                next_maturity=str(investment.maturity_date),
            )
        else:
            investment.status = "matured"
            log.info("bank_investment_matured", investment_id=investment.id)
    session.flush()


def _close_term(session: Session, investment: BankInvestment, closed_on: date) -> None:
    """Seal the term that just matured (append-only: nothing is rewritten).
    Investments created before the history table carry no rows yet, so the
    finished term is synthesized from the parent row before it mutates."""
    open_term = session.scalar(
        select(BankInvestmentTerm)
        .where(
            BankInvestmentTerm.investment_id == investment.id,
            BankInvestmentTerm.end_date.is_(None),
        )
        .limit(1)
    )
    if open_term is not None:
        open_term.end_date = closed_on
        return
    session.add(
        BankInvestmentTerm(
            investment_id=investment.id,
            start_date=investment.start_date,
            end_date=closed_on,
            principal=investment.principal,
            annual_rate=investment.annual_rate,
            rate_tiers=investment.rate_tiers,
            cap_amount=investment.cap_amount,
        )
    )


def _send_reminders(session: Session) -> int:
    from app.briefing.morning import is_enabled as brief_enabled

    if brief_enabled(session):
        # rollovers/flips above still ran — only the standalone reminder message
        # is suppressed; the 08:45 morning brief lists upcoming maturities
        return 0
    today = date.today()
    horizon = today + timedelta(days=get_settings().maturity_reminder_days)
    upcoming = session.execute(
        select(BankInvestment, Account.name)
        .join(Account, Account.id == BankInvestment.account_id)
        .where(
            BankInvestment.status == "active",
            BankInvestment.kind == "fixed_term",
            BankInvestment.maturity_date > today,
            BankInvestment.maturity_date <= horizon,
        )
        .order_by(BankInvestment.maturity_date)
    ).all()

    sent = 0
    for investment, account_name in upcoming:
        if _already_reminded(session, investment):
            continue
        days_left = (investment.maturity_date - today).days
        value = projected_maturity_value(_terms(investment))
        subject = f"Plutus: {investment.name} matures in {days_left} day(s)"
        body = (
            f"{investment.name} ({account_name}) matures on {investment.maturity_date}.\n"
            f"Principal: {float(investment.principal):,.2f} {investment.currency} — "
            f"value at maturity: {value:,.2f} {investment.currency}.\n"
            + (
                "It will auto-renew for another term."
                if investment.auto_renew
                else "It will NOT auto-renew — decide where the money goes next."
            )
        )
        results = deliver(
            session,
            "maturity",
            subject,
            body,
            meta={
                "investment_id": investment.id,
                "maturity_date": investment.maturity_date.isoformat(),
            },
        )
        if any(r["ok"] for r in results):
            sent += 1
    return sent


def _already_reminded(session: Session, investment: BankInvestment) -> bool:
    """One reminder per investment per maturity date (renewals remind again)."""
    return (
        session.scalar(
            select(Notification.id)
            .where(
                Notification.kind == "maturity",
                Notification.ok.is_(True),
                Notification.meta["investment_id"].as_integer() == investment.id,
                Notification.meta["maturity_date"].as_string()
                == investment.maturity_date.isoformat(),
            )
            .limit(1)
        )
        is not None
    )
