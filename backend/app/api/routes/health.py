from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.health.aggregate import ingestion_health

# Authenticated pipeline health under /api/v1 — distinct from the bare /health
# liveness probe mounted on the raw app in main.py.
router = APIRouter(prefix="/health", tags=["health"])


@router.get("/ingestion")
def get_ingestion_health(db: Session = Depends(get_db)):
    return ingestion_health(db)
