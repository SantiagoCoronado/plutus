from fastapi import APIRouter, Depends

from app.api.deps import require_auth
from app.api.routes import (
    accounts,
    agent,
    agent_settings,
    alerts,
    assets,
    backtests,
    bank_investments,
    brief,
    candidates,
    dashboard,
    exchanges,
    health,
    ingestion,
    mandates,
    portfolio,
    research,
    screens,
    transactions,
    translations,
    watchlists,
    ws_quotes,
)

api_router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_auth)])
api_router.include_router(assets.router)
api_router.include_router(research.router)
api_router.include_router(watchlists.router)
api_router.include_router(screens.router)
api_router.include_router(backtests.router)
api_router.include_router(mandates.router)
api_router.include_router(candidates.router)
api_router.include_router(accounts.router)
api_router.include_router(transactions.router)
api_router.include_router(bank_investments.router)
api_router.include_router(portfolio.router)
api_router.include_router(dashboard.router)
api_router.include_router(ingestion.router)
api_router.include_router(agent_settings.router)
api_router.include_router(agent.router)
api_router.include_router(translations.router)
api_router.include_router(alerts.router)
api_router.include_router(exchanges.router)
api_router.include_router(health.router)
api_router.include_router(ws_quotes.router)
api_router.include_router(brief.router)


@api_router.get("/ping", tags=["meta"])
def ping():
    """Authenticated no-op; lets clients validate their token without touching the DB."""
    return {"pong": True}
