from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, create_model

from app.models import METRIC_COLUMNS


class _AssetMetricsBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    asset_id: int
    as_of: date
    computed_at: datetime
    benchmark_symbol: str | None
    extras: dict


# one field per METRIC_COLUMNS entry — stays in lockstep with the model/migration
AssetMetricsOut = create_model(
    "AssetMetricsOut",
    __base__=_AssetMetricsBase,
    **{name: (float | None, None) for name in METRIC_COLUMNS},
)
