from fastapi import APIRouter, Depends

from app.api.deps import require_auth
from app.api.routes import (
    accounts,
    agent,
    agent_settings,
    assets,
    backtests,
    bank_investments,
    candidates,
    ingestion,
    mandates,
    portfolio,
    research,
    screens,
    transactions,
    watchlists,
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
api_router.include_router(ingestion.router)
api_router.include_router(agent_settings.router)
api_router.include_router(agent.router)


@api_router.get("/ping", tags=["meta"])
def ping():
    """Authenticated no-op; lets clients validate their token without touching the DB."""
    return {"pong": True}
