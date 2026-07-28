"""Execution Supervisor policies, incidents and actions

Revision ID: 20260715_0017
Revises: 20260715_0016
Create Date: 2026-07-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0017"
down_revision: Union[str, Sequence[str], None] = "20260715_0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "execution_supervisor_policies",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("scope_key", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("automatic_actions_enabled", sa.Boolean(), nullable=False),
        sa.Column("check_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("stall_timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("max_step_runtime_seconds", sa.Integer(), nullable=False),
        sa.Column("retry_warning_threshold", sa.Integer(), nullable=False),
        sa.Column("failure_action", sa.String(length=32), nullable=False),
        sa.Column("stall_action", sa.String(length=32), nullable=False),
        sa.Column("long_running_action", sa.String(length=32), nullable=False),
        sa.Column("retry_action", sa.String(length=32), nullable=False),
        sa.Column("auto_start_replanned", sa.Boolean(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "failure_action IN ('observe', 'notify', 'cancel', 'replan')",
            name="ck_execution_supervisor_policies_failure_action",
        ),
        sa.CheckConstraint(
            "stall_action IN ('observe', 'notify', 'cancel', 'replan')",
            name="ck_execution_supervisor_policies_stall_action",
        ),
        sa.CheckConstraint(
            "long_running_action IN ('observe', 'notify', 'cancel', 'replan')",
            name="ck_execution_supervisor_policies_long_action",
        ),
        sa.CheckConstraint(
            "retry_action IN ('observe', 'notify', 'cancel', 'replan')",
            name="ck_execution_supervisor_policies_retry_action",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scope_key",
            name="uq_execution_supervisor_policies_scope_key",
        ),
    )
    op.create_index(
        "ix_execution_supervisor_policies_scope_key",
        "execution_supervisor_policies",
        ["scope_key"],
    )
    op.create_index(
        "ix_execution_supervisor_policies_workspace_id",
        "execution_supervisor_policies",
        ["workspace_id"],
    )
    op.create_index(
        "ix_execution_supervisor_policies_enabled",
        "execution_supervisor_policies",
        ["enabled"],
    )
    op.create_index(
        "ix_execution_supervisor_policies_created_at",
        "execution_supervisor_policies",
        ["created_at"],
    )

    op.create_table(
        "execution_supervisor_incidents",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("step_id", sa.String(length=64), nullable=True),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("policy_id", sa.String(length=64), nullable=True),
        sa.Column("incident_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("dedupe_key", sa.String(length=512), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(length=255), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=255), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'high', 'critical')",
            name="ck_execution_supervisor_incidents_severity",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved', 'dismissed')",
            name="ck_execution_supervisor_incidents_status",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["execution_plans.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["step_id"],
            ["execution_plan_steps.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["execution_supervisor_policies.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dedupe_key",
            name="uq_execution_supervisor_incidents_dedupe_key",
        ),
    )
    for name in (
        "plan_id",
        "step_id",
        "workspace_id",
        "policy_id",
        "incident_type",
        "severity",
        "status",
        "detected_at",
        "created_at",
    ):
        op.create_index(
            f"ix_execution_supervisor_incidents_{name}",
            "execution_supervisor_incidents",
            [name],
        )

    op.create_table(
        "execution_supervisor_actions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("incident_id", sa.String(length=64), nullable=True),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("step_id", sa.String(length=64), nullable=True),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("policy_id", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=512), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('observe', 'notify', 'cancel', 'replan')",
            name="ck_execution_supervisor_actions_action",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'skipped')",
            name="ck_execution_supervisor_actions_status",
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["execution_supervisor_incidents.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["execution_plans.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["step_id"],
            ["execution_plan_steps.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["execution_supervisor_policies.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dedupe_key",
            name="uq_execution_supervisor_actions_dedupe_key",
        ),
    )
    for name in (
        "incident_id",
        "plan_id",
        "step_id",
        "workspace_id",
        "policy_id",
        "action",
        "status",
        "automatic",
        "created_at",
    ):
        op.create_index(
            f"ix_execution_supervisor_actions_{name}",
            "execution_supervisor_actions",
            [name],
        )


def downgrade() -> None:
    for name in reversed(
        (
            "incident_id",
            "plan_id",
            "step_id",
            "workspace_id",
            "policy_id",
            "action",
            "status",
            "automatic",
            "created_at",
        )
    ):
        op.drop_index(
            f"ix_execution_supervisor_actions_{name}",
            table_name="execution_supervisor_actions",
        )
    op.drop_table("execution_supervisor_actions")

    for name in reversed(
        (
            "plan_id",
            "step_id",
            "workspace_id",
            "policy_id",
            "incident_type",
            "severity",
            "status",
            "detected_at",
            "created_at",
        )
    ):
        op.drop_index(
            f"ix_execution_supervisor_incidents_{name}",
            table_name="execution_supervisor_incidents",
        )
    op.drop_table("execution_supervisor_incidents")

    op.drop_index(
        "ix_execution_supervisor_policies_created_at",
        table_name="execution_supervisor_policies",
    )
    op.drop_index(
        "ix_execution_supervisor_policies_enabled",
        table_name="execution_supervisor_policies",
    )
    op.drop_index(
        "ix_execution_supervisor_policies_workspace_id",
        table_name="execution_supervisor_policies",
    )
    op.drop_index(
        "ix_execution_supervisor_policies_scope_key",
        table_name="execution_supervisor_policies",
    )
    op.drop_table("execution_supervisor_policies")
