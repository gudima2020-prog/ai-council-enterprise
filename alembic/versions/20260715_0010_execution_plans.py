"""Execution plans core

Revision ID: 20260715_0010
Revises: 20260715_0009
Create Date: 2026-07-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0010"
down_revision: Union[str, Sequence[str], None] = "20260715_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "execution_plans",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("source_task_id", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column(
            "strategy",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "max_parallel_steps",
            sa.Integer(),
            nullable=False,
            server_default="4",
        ),
        sa.Column("planner", sa.String(length=255), nullable=True),
        sa.Column(
            "validation_json",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "metadata_json",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
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
        sa.Column(
            "validated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ("
            "'draft', 'validated', 'ready', 'running', "
            "'completed', 'failed', 'cancelled', 'superseded'"
            ")",
            name="ck_execution_plans_status",
        ),
        sa.ForeignKeyConstraint(
            ["source_task_id"],
            ["tasks.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_execution_plans_workspace_id",
        "execution_plans",
        ["workspace_id"],
    )
    op.create_index(
        "ix_execution_plans_source_task_id",
        "execution_plans",
        ["source_task_id"],
    )
    op.create_index(
        "ix_execution_plans_status",
        "execution_plans",
        ["status"],
    )
    op.create_index(
        "ix_execution_plans_created_at",
        "execution_plans",
        ["created_at"],
    )

    op.create_table(
        "execution_plan_steps",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("step_key", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "step_type",
            sa.String(length=32),
            nullable=False,
            server_default="agent",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column(
            "description",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
        sa.Column("agent_role", sa.String(length=255), nullable=True),
        sa.Column("capability", sa.String(length=255), nullable=True),
        sa.Column("tool_name", sa.String(length=255), nullable=True),
        sa.Column(
            "input_json",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column(
            "depends_on_json",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("condition_json", sa.JSON(), nullable=True),
        sa.Column(
            "timeout_seconds",
            sa.Integer(),
            nullable=False,
            server_default="300",
        ),
        sa.Column(
            "max_retries",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "metadata_json",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
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
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "step_type IN ("
            "'agent', 'tool', 'task', 'approval', "
            "'decision', 'checkpoint'"
            ")",
            name="ck_execution_plan_steps_type",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'pending', 'ready', 'running', 'completed', "
            "'failed', 'skipped', 'cancelled'"
            ")",
            name="ck_execution_plan_steps_status",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["execution_plans.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_id",
            "step_key",
            name="uq_execution_plan_steps_key",
        ),
    )
    op.create_index(
        "ix_execution_plan_steps_plan_id",
        "execution_plan_steps",
        ["plan_id"],
    )
    op.create_index(
        "ix_execution_plan_steps_step_type",
        "execution_plan_steps",
        ["step_type"],
    )
    op.create_index(
        "ix_execution_plan_steps_status",
        "execution_plan_steps",
        ["status"],
    )
    op.create_index(
        "ix_execution_plan_steps_agent_role",
        "execution_plan_steps",
        ["agent_role"],
    )
    op.create_index(
        "ix_execution_plan_steps_capability",
        "execution_plan_steps",
        ["capability"],
    )
    op.create_index(
        "ix_execution_plan_steps_tool_name",
        "execution_plan_steps",
        ["tool_name"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_execution_plan_steps_tool_name",
        table_name="execution_plan_steps",
    )
    op.drop_index(
        "ix_execution_plan_steps_capability",
        table_name="execution_plan_steps",
    )
    op.drop_index(
        "ix_execution_plan_steps_agent_role",
        table_name="execution_plan_steps",
    )
    op.drop_index(
        "ix_execution_plan_steps_status",
        table_name="execution_plan_steps",
    )
    op.drop_index(
        "ix_execution_plan_steps_step_type",
        table_name="execution_plan_steps",
    )
    op.drop_index(
        "ix_execution_plan_steps_plan_id",
        table_name="execution_plan_steps",
    )
    op.drop_table("execution_plan_steps")

    op.drop_index(
        "ix_execution_plans_created_at",
        table_name="execution_plans",
    )
    op.drop_index(
        "ix_execution_plans_status",
        table_name="execution_plans",
    )
    op.drop_index(
        "ix_execution_plans_source_task_id",
        table_name="execution_plans",
    )
    op.drop_index(
        "ix_execution_plans_workspace_id",
        table_name="execution_plans",
    )
    op.drop_table("execution_plans")
