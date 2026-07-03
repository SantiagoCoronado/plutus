from datetime import datetime

from pydantic import BaseModel, ConfigDict


class IngestionRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_name: str
    provider: str | None
    asset_class: str | None
    started_at: datetime
    finished_at: datetime | None
    status: str
    rows_written: int
    symbols_ok: int
    symbols_failed: int
    details: dict
