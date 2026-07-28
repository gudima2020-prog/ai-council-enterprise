"""P2-003 add cancelled Council run status.

Revision ID: 20260724_0045
Revises: 20260716_0044
"""
from __future__ import annotations

from alembic import op


revision = "20260724_0045"
down_revision = "20260716_0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table(
        "council_runs",
        recreate="always",
    ) as batch_op:
        batch_op.drop_constraint(
            "ck_council_runs_status",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_council_runs_status",
            "status IN "
            "('completed', 'partial', 'failed', 'cancelled')",
        )


def downgrade() -> None:
    op.execute(
        "UPDATE council_runs SET status = 'failed' "
        "WHERE status = 'cancelled'"
    )
    with op.batch_alter_table(
        "council_runs",
        recreate="always",
    ) as batch_op:
        batch_op.drop_constraint(
            "ck_council_runs_status",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_council_runs_status",
            "status IN ('completed', 'partial', 'failed')",
        )
