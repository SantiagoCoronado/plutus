from fastapi import APIRouter, Depends

from app.api.deps import require_auth
from app.api.routes import (
    assets,
    backtests,
    candidates,
    ingestion,
    mandates,
    research,
    screens,
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
api_router.include_router(ingestion.router)


@api_router.get("/ping", tags=["meta"])
def ping():
    """Authenticated no-op; lets clients validate their token without touching the DB."""
    return {"pong": True}
