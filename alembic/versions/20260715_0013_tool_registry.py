"""Tool registry, permissions and invocations

Revision ID: 20260715_0013
Revises: 20260715_0012
Create Date: 2026-07-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0013"
down_revision: Union[str, Sequence[str], None] = "20260715_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tool_definitions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("tool_key", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("handler_ref", sa.String(length=255), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("requires_explicit_allow", sa.Boolean(), nullable=False),
        sa.Column("isolation_mode", sa.String(length=32), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("max_concurrency", sa.Integer(), nullable=False),
        sa.Column("max_input_bytes", sa.Integer(), nullable=False),
        sa.Column("max_output_bytes", sa.Integer(), nullable=False),
        sa.Column("allow_network", sa.Boolean(), nullable=False),
        sa.Column("allow_filesystem_read", sa.Boolean(), nullable=False),
        sa.Column("allow_filesystem_write", sa.Boolean(), nullable=False),
        sa.Column("input_schema_json", sa.JSON(), nullable=False),
        sa.Column("output_schema_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('builtin', 'plugin', 'http', 'subprocess')",
            name="ck_tool_definitions_kind",
        ),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="ck_tool_definitions_risk",
        ),
        sa.CheckConstraint(
            "isolation_mode IN ('restricted', 'trusted')",
            name="ck_tool_definitions_isolation",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "tool_key",
            name="uq_tool_definitions_workspace_key",
        ),
    )
    for name, columns in (
        ("ix_tool_definitions_workspace_id", ["workspace_id"]),
        ("ix_tool_definitions_tool_key", ["tool_key"]),
        ("ix_tool_definitions_kind", ["kind"]),
        ("ix_tool_definitions_handler_ref", ["handler_ref"]),
        ("ix_tool_definitions_risk_level", ["risk_level"]),
        ("ix_tool_definitions_enabled", ["enabled"]),
        ("ix_tool_definitions_created_at", ["created_at"]),
    ):
        op.create_index(name, "tool_definitions", columns, unique=False)

    op.create_table(
        "tool_permissions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tool_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("agent_id", sa.String(length=64), nullable=True),
        sa.Column("effect", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("constraints_json", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "effect IN ('allow', 'deny')",
            name="ck_tool_permissions_effect",
        ),
        sa.CheckConstraint(
            "action IN ('execute')",
            name="ck_tool_permissions_action",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agent_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tool_id"],
            ["tool_definitions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in (
        ("ix_tool_permissions_tool_id", ["tool_id"]),
        ("ix_tool_permissions_workspace_id", ["workspace_id"]),
        ("ix_tool_permissions_agent_id", ["agent_id"]),
        ("ix_tool_permissions_effect", ["effect"]),
        ("ix_tool_permissions_expires_at", ["expires_at"]),
        ("ix_tool_permissions_created_at", ["created_at"]),
    ):
        op.create_index(name, "tool_permissions", columns, unique=False)

    op.create_table(
        "tool_invocations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tool_id", sa.String(length=64), nullable=True),
        sa.Column("tool_key", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("agent_id", sa.String(length=64), nullable=True),
        sa.Column("plan_id", sa.String(length=64), nullable=True),
        sa.Column("step_id", sa.String(length=64), nullable=True),
        sa.Column("correlation_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("isolation_mode", sa.String(length=32), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("policy_json", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'denied', "
            "'timed_out', 'cancelled')",
            name="ck_tool_invocations_status",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agent_profiles.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["execution_plans.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["step_id"],
            ["execution_plan_steps.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tool_id"],
            ["tool_definitions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in (
        ("ix_tool_invocations_tool_id", ["tool_id"]),
        ("ix_tool_invocations_tool_key", ["tool_key"]),
        ("ix_tool_invocations_workspace_id", ["workspace_id"]),
        ("ix_tool_invocations_agent_id", ["agent_id"]),
        ("ix_tool_invocations_plan_id", ["plan_id"]),
        ("ix_tool_invocations_step_id", ["step_id"]),
        ("ix_tool_invocations_correlation_id", ["correlation_id"]),
        ("ix_tool_invocations_status", ["status"]),
        ("ix_tool_invocations_started_at", ["started_at"]),
        ("ix_tool_invocations_created_at", ["created_at"]),
    ):
        op.create_index(name, "tool_invocations", columns, unique=False)


def downgrade() -> None:
    op.drop_table("tool_invocations")
    op.drop_table("tool_permissions")
    op.drop_table("tool_definitions")
