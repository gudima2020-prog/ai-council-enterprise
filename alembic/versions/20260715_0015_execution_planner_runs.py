"""Execution Planner runs

Revision ID: 20260715_0015
Revises: 20260715_0014
Create Date: 2026-07-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0015"
down_revision: Union[str, Sequence[str], None] = "20260715_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "execution_planner_runs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("source_task_id", sa.String(length=64), nullable=True),
        sa.Column("source_plan_id", sa.String(length=64), nullable=True),
        sa.Column("result_plan_id", sa.String(length=64), nullable=True),
        sa.Column("run_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("planner_ref", sa.String(length=255), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("failure_context_json", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
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
            "run_type IN ('generate', 'replan')",
            name="ck_execution_planner_runs_type",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_execution_planner_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_task_id"],
            ["tasks.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_plan_id"],
            ["execution_plans.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["result_plan_id"],
            ["execution_plans.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_execution_planner_runs_workspace_id",
        "execution_planner_runs",
        ["workspace_id"],
    )
    op.create_index(
        "ix_execution_planner_runs_source_task_id",
        "execution_planner_runs",
        ["source_task_id"],
    )
    op.create_index(
        "ix_execution_planner_runs_source_plan_id",
        "execution_planner_runs",
        ["source_plan_id"],
    )
    op.create_index(
        "ix_execution_planner_runs_result_plan_id",
        "execution_planner_runs",
        ["result_plan_id"],
    )
    op.create_index(
        "ix_execution_planner_runs_run_type",
        "execution_planner_runs",
        ["run_type"],
    )
    op.create_index(
        "ix_execution_planner_runs_status",
        "execution_planner_runs",
        ["status"],
    )
    op.create_index(
        "ix_execution_planner_runs_planner_ref",
        "execution_planner_runs",
        ["planner_ref"],
    )
    op.create_index(
        "ix_execution_planner_runs_created_at",
        "execution_planner_runs",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_execution_planner_runs_created_at",
        table_name="execution_planner_runs",
    )
    op.drop_index(
        "ix_execution_planner_runs_planner_ref",
        table_name="execution_planner_runs",
    )
    op.drop_index(
        "ix_execution_planner_runs_status",
        table_name="execution_planner_runs",
    )
    op.drop_index(
        "ix_execution_planner_runs_run_type",
        table_name="execution_planner_runs",
    )
    op.drop_index(
        "ix_execution_planner_runs_result_plan_id",
        table_name="execution_planner_runs",
    )
    op.drop_index(
        "ix_execution_planner_runs_source_plan_id",
        table_name="execution_planner_runs",
    )
    op.drop_index(
        "ix_execution_planner_runs_source_task_id",
        table_name="execution_planner_runs",
    )
    op.drop_index(
        "ix_execution_planner_runs_workspace_id",
        table_name="execution_planner_runs",
    )
    op.drop_table("execution_planner_runs")
