"""Phase 12: allow the morning_brief notification kind

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-15

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = (
    "kind IN ('instant','digest','test','maturity','memo','price_alert',"
    "'task_failure','watchdog')"
)
NEW = (
    "kind IN ('instant','digest','test','maturity','memo','price_alert',"
    "'task_failure','watchdog','morning_brief')"
)


def upgrade() -> None:
    op.drop_constraint("ck_notifications_kind", "notifications", type_="check")
    op.create_check_constraint("ck_notifications_kind", "notifications", sa.text(NEW))


def downgrade() -> None:
    op.drop_constraint("ck_notifications_kind", "notifications", type_="check")
    op.create_check_constraint("ck_notifications_kind", "notifications", sa.text(OLD))
