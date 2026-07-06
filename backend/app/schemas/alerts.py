from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AlertCondition = Literal["above", "below"]
AlertStatus = Literal["armed", "triggered", "disabled"]
# the client may only arm or disable; 'triggered' is the evaluator's to set
ClientAlertStatus = Literal["armed", "disabled"]


class AlertRuleIn(BaseModel):
    asset_id: int
    condition: AlertCondition
    threshold: float = Field(gt=0)
    note: str | None = None


class AlertRulePatch(BaseModel):
    """Partial edit: re-arm/disable, or adjust the condition/threshold/note."""

    status: ClientAlertStatus | None = None
    condition: AlertCondition | None = None
    threshold: float | None = Field(default=None, gt=0)
    note: str | None = None


class AlertRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    asset_id: int
    condition: str
    threshold: float
    status: str
    cooldown_minutes: int | None
    last_price: float | None
    last_triggered_at: datetime | None
    note: str | None
    created_at: datetime
    updated_at: datetime
    # joined by the route for display
    symbol: str | None = None
    name: str | None = None
