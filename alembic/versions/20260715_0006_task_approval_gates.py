"""Task approval gates

Revision ID: 20260715_0006
Revises: 20260715_0005
Create Date: 2026-07-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0006"
down_revision: Union[str, Sequence[str], None] = "20260715_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_approvals",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("gate_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.String(length=255), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id",
            "gate_key",
            name="uq_task_approvals_task_gate",
        ),
    )
    op.create_index(
        "ix_task_approvals_task_id",
        "task_approvals",
        ["task_id"],
    )
    op.create_index(
        "ix_task_approvals_workspace_id",
        "task_approvals",
        ["workspace_id"],
    )
    op.create_index(
        "ix_task_approvals_status",
        "task_approvals",
        ["status"],
    )
    op.create_index(
        "ix_task_approvals_expires_at",
        "task_approvals",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_task_approvals_expires_at",
        table_name="task_approvals",
    )
    op.drop_index(
        "ix_task_approvals_status",
        table_name="task_approvals",
    )
    op.drop_index(
        "ix_task_approvals_workspace_id",
        table_name="task_approvals",
    )
    op.drop_index(
        "ix_task_approvals_task_id",
        table_name="task_approvals",
    )
    op.drop_table("task_approvals")
