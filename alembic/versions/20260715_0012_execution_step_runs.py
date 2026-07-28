"""Execution Plan runtime and persistent step runs.

Revision ID: 20260715_0012
Revises: 20260715_0011
Create Date: 2026-07-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0012"
down_revision: Union[str, Sequence[str], None] = "20260715_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "execution_step_runs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("step_id", sa.String(length=64), nullable=False),
        sa.Column("step_key", sa.String(length=64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="running",
        ),
        sa.Column("agent_id", sa.String(length=64), nullable=True),
        sa.Column("executor_ref", sa.String(length=255), nullable=True),
        sa.Column(
            "input_json",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "metadata_json",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'cancelled')",
            name="ck_execution_step_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["execution_plans.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["step_id"],
            ["execution_plan_steps.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "step_id",
            "attempt",
            name="uq_execution_step_runs_attempt",
        ),
    )
    op.create_index(
        "ix_execution_step_runs_plan_id",
        "execution_step_runs",
        ["plan_id"],
    )
    op.create_index(
        "ix_execution_step_runs_step_id",
        "execution_step_runs",
        ["step_id"],
    )
    op.create_index(
        "ix_execution_step_runs_step_key",
        "execution_step_runs",
        ["step_key"],
    )
    op.create_index(
        "ix_execution_step_runs_status",
        "execution_step_runs",
        ["status"],
    )
    op.create_index(
        "ix_execution_step_runs_agent_id",
        "execution_step_runs",
        ["agent_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_execution_step_runs_agent_id",
        table_name="execution_step_runs",
    )
    op.drop_index(
        "ix_execution_step_runs_status",
        table_name="execution_step_runs",
    )
    op.drop_index(
        "ix_execution_step_runs_step_key",
        table_name="execution_step_runs",
    )
    op.drop_index(
        "ix_execution_step_runs_step_id",
        table_name="execution_step_runs",
    )
    op.drop_index(
        "ix_execution_step_runs_plan_id",
        table_name="execution_step_runs",
    )
    op.drop_table("execution_step_runs")
