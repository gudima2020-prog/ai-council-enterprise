"""Task execution policy

Revision ID: 20260715_0003
Revises: 20260715_0002
Create Date: 2026-07-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0003"
down_revision: Union[str, Sequence[str], None] = "20260715_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(
            sa.Column(
                "retry_delay_seconds",
                sa.Float(),
                nullable=False,
                server_default="1.0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "timeout_seconds",
                sa.Integer(),
                nullable=False,
                server_default="300",
            )
        )
        batch_op.add_column(
            sa.Column(
                "cancel_requested_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "cancel_reason",
                sa.Text(),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("cancel_reason")
        batch_op.drop_column("cancel_requested_at")
        batch_op.drop_column("timeout_seconds")
        batch_op.drop_column("retry_delay_seconds")
