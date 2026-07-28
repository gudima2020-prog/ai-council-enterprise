"""Task dependencies and workflow DAG

Revision ID: 20260715_0004
Revises: 20260715_0003
Create Date: 2026-07-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0004"
down_revision: Union[str, Sequence[str], None] = "20260715_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_dependencies",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column(
            "depends_on_task_id",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "dependency_type",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "required_status",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "task_id <> depends_on_task_id",
            name="ck_task_dependencies_not_self",
        ),
        sa.ForeignKeyConstraint(
            ["depends_on_task_id"],
            ["tasks.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id",
            "depends_on_task_id",
            name="uq_task_dependencies_pair",
        ),
    )
    op.create_index(
        "ix_task_dependencies_task_id",
        "task_dependencies",
        ["task_id"],
    )
    op.create_index(
        "ix_task_dependencies_depends_on_task_id",
        "task_dependencies",
        ["depends_on_task_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_task_dependencies_depends_on_task_id",
        table_name="task_dependencies",
    )
    op.drop_index(
        "ix_task_dependencies_task_id",
        table_name="task_dependencies",
    )
    op.drop_table("task_dependencies")
