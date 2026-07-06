from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import AlertRule, Asset
from app.schemas.alerts import AlertRuleIn, AlertRuleOut, AlertRulePatch

router = APIRouter(prefix="/alerts", tags=["alerts"])


# --- reusable, non-HTTP helpers (shared with the agent tool handlers) ---------
# validate_asset_or_404 raises HTTPException; the tool handlers catch it and turn
# it into a model-fixable ToolInputError (the mandates.py precedent).


def get_alert_or_404(db: Session, alert_id: int) -> AlertRule:
    rule = db.get(AlertRule, alert_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="alert rule not found")
    return rule


def validate_asset_or_404(db: Session, asset_id: int) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None or not asset.is_active:
        raise HTTPException(status_code=404, detail="asset not found or inactive")
    return asset


def create_alert(db: Session, body: AlertRuleIn) -> AlertRule:
    """Validate the asset, build the rule, and flush. The caller commits."""
    validate_asset_or_404(db, body.asset_id)
    rule = AlertRule(
        asset_id=body.asset_id,
        condition=body.condition,
        threshold=body.threshold,
        note=body.note,
    )
    db.add(rule)
    db.flush()
    return rule


def apply_patch(rule: AlertRule, patch: AlertRulePatch) -> None:
    """Edit a rule in place, preserving the evaluator's crossing-edge semantics:
    a re-arm (status→armed) or a change to condition/threshold on an armed rule
    clears last_price so the next evaluation re-baselines instead of firing."""
    edited_trigger = False
    if patch.condition is not None:
        rule.condition = patch.condition
        edited_trigger = True
    if patch.threshold is not None:
        rule.threshold = patch.threshold
        edited_trigger = True
    if patch.note is not None:
        rule.note = patch.note
    if patch.status is not None:
        rule.status = patch.status
        if patch.status == "armed":
            rule.last_price = None
            rule.last_triggered_at = None
    if edited_trigger and rule.status == "armed":
        rule.last_price = None


def _to_out(rule: AlertRule, symbol: str | None, name: str | None) -> AlertRuleOut:
    out = AlertRuleOut.model_validate(rule)
    out.symbol = symbol
    out.name = name
    return out


@router.get("", response_model=list[AlertRuleOut])
def list_alerts(
    db: Session = Depends(get_db),
    asset_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
):
    stmt = (
        select(AlertRule, Asset.symbol, Asset.name)
        .join(Asset, Asset.id == AlertRule.asset_id)
        .order_by(AlertRule.created_at.desc(), AlertRule.id.desc())
    )
    if asset_id is not None:
        stmt = stmt.where(AlertRule.asset_id == asset_id)
    if status is not None:
        if status not in ("armed", "triggered", "disabled"):
            raise HTTPException(status_code=422, detail="invalid status filter")
        stmt = stmt.where(AlertRule.status == status)
    return [_to_out(rule, symbol, name) for rule, symbol, name in db.execute(stmt).all()]


@router.post("", response_model=AlertRuleOut, status_code=201)
def create_alert_rule(body: AlertRuleIn, db: Session = Depends(get_db)):
    rule = create_alert(db, body)
    asset = db.get(Asset, rule.asset_id)
    db.commit()
    db.refresh(rule)
    return _to_out(rule, asset.symbol if asset else None, asset.name if asset else None)


@router.patch("/{alert_id}", response_model=AlertRuleOut)
def update_alert_rule(alert_id: int, patch: AlertRulePatch, db: Session = Depends(get_db)):
    rule = get_alert_or_404(db, alert_id)
    apply_patch(rule, patch)
    db.commit()
    db.refresh(rule)
    asset = db.get(Asset, rule.asset_id)
    return _to_out(rule, asset.symbol if asset else None, asset.name if asset else None)


@router.delete("/{alert_id}", status_code=204)
def delete_alert_rule(alert_id: int, db: Session = Depends(get_db)):
    rule = get_alert_or_404(db, alert_id)
    db.delete(rule)
    db.commit()
