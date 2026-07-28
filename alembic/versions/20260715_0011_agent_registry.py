"""Agent registry, capabilities and execution step assignments.

Revision ID: 20260715_0011
Revises: 20260715_0010
Create Date: 2026-07-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0011"
down_revision: Union[str, Sequence[str], None] = "20260715_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_profiles",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("agent_key", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("roles_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("tools_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="available"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("max_concurrency", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("executor_ref", sa.String(length=255), nullable=False),
        sa.Column("model_slug", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('available', 'busy', 'offline', 'maintenance')",
            name="ck_agent_profiles_status",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agent_key",
            name="uq_agent_profiles_agent_key",
        ),
    )
    op.create_index(
        "ix_agent_profiles_workspace_id",
        "agent_profiles",
        ["workspace_id"],
    )
    op.create_index(
        "ix_agent_profiles_agent_key",
        "agent_profiles",
        ["agent_key"],
    )
    op.create_index(
        "ix_agent_profiles_enabled",
        "agent_profiles",
        ["enabled"],
    )
    op.create_index(
        "ix_agent_profiles_status",
        "agent_profiles",
        ["status"],
    )
    op.create_index(
        "ix_agent_profiles_created_at",
        "agent_profiles",
        ["created_at"],
    )

    op.create_table(
        "agent_capabilities",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("agent_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("proficiency", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "proficiency >= 0 AND proficiency <= 100",
            name="ck_agent_capabilities_proficiency",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agent_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agent_id",
            "name",
            name="uq_agent_capabilities_agent_name",
        ),
    )
    op.create_index(
        "ix_agent_capabilities_agent_id",
        "agent_capabilities",
        ["agent_id"],
    )
    op.create_index(
        "ix_agent_capabilities_name",
        "agent_capabilities",
        ["name"],
    )
    op.create_index(
        "ix_agent_capabilities_enabled",
        "agent_capabilities",
        ["enabled"],
    )

    op.add_column(
        "execution_plan_steps",
        sa.Column("assigned_agent_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "execution_plan_steps",
        sa.Column(
            "assignment_json",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "execution_plan_steps",
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_execution_plan_steps_assigned_agent_id",
        "execution_plan_steps",
        ["assigned_agent_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_execution_plan_steps_assigned_agent_id",
        table_name="execution_plan_steps",
    )
    op.drop_column("execution_plan_steps", "assigned_at")
    op.drop_column("execution_plan_steps", "assignment_json")
    op.drop_column("execution_plan_steps", "assigned_agent_id")

    op.drop_index("ix_agent_capabilities_enabled", table_name="agent_capabilities")
    op.drop_index("ix_agent_capabilities_name", table_name="agent_capabilities")
    op.drop_index("ix_agent_capabilities_agent_id", table_name="agent_capabilities")
    op.drop_table("agent_capabilities")

    op.drop_index("ix_agent_profiles_created_at", table_name="agent_profiles")
    op.drop_index("ix_agent_profiles_status", table_name="agent_profiles")
    op.drop_index("ix_agent_profiles_enabled", table_name="agent_profiles")
    op.drop_index("ix_agent_profiles_agent_key", table_name="agent_profiles")
    op.drop_index("ix_agent_profiles_workspace_id", table_name="agent_profiles")
    op.drop_table("agent_profiles")
