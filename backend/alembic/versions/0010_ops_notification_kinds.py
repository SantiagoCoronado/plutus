"""Phase 10 M3: allow task_failure + watchdog notification kinds

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-13

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "kind IN ('instant','digest','test','maturity','memo','price_alert')"
NEW = (
    "kind IN ('instant','digest','test','maturity','memo','price_alert',"
    "'task_failure','watchdog')"
)


def upgrade() -> None:
    op.drop_constraint("ck_notifications_kind", "notifications", type_="check")
    op.create_check_constraint("ck_notifications_kind", "notifications", sa.text(NEW))


def downgrade() -> None:
    op.drop_constraint("ck_notifications_kind", "notifications", type_="check")
    op.create_check_constraint("ck_notifications_kind", "notifications", sa.text(OLD))
