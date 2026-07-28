"""Workflow templates and instances

Revision ID: 20260715_0005
Revises: 20260715_0004
Create Date: 2026-07-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0005"
down_revision: Union[str, Sequence[str], None] = "20260715_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_templates",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("definition_json", sa.JSON(), nullable=False),
        sa.Column("input_schema_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "name",
            "version",
            name="uq_workflow_templates_workspace_name_version",
        ),
    )
    op.create_index(
        "ix_workflow_templates_workspace_id",
        "workflow_templates",
        ["workspace_id"],
    )
    op.create_index(
        "ix_workflow_templates_name",
        "workflow_templates",
        ["name"],
    )
    op.create_index(
        "ix_workflow_templates_enabled",
        "workflow_templates",
        ["enabled"],
    )

    op.create_table(
        "workflow_instances",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("template_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("root_task_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("node_task_map_json", sa.JSON(), nullable=False),
        sa.Column("context_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["root_task_id"],
            ["tasks.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["workflow_templates.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workflow_instances_template_id",
        "workflow_instances",
        ["template_id"],
    )
    op.create_index(
        "ix_workflow_instances_workspace_id",
        "workflow_instances",
        ["workspace_id"],
    )
    op.create_index(
        "ix_workflow_instances_root_task_id",
        "workflow_instances",
        ["root_task_id"],
    )
    op.create_index(
        "ix_workflow_instances_status",
        "workflow_instances",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_instances_status", table_name="workflow_instances")
    op.drop_index("ix_workflow_instances_root_task_id", table_name="workflow_instances")
    op.drop_index("ix_workflow_instances_workspace_id", table_name="workflow_instances")
    op.drop_index("ix_workflow_instances_template_id", table_name="workflow_instances")
    op.drop_table("workflow_instances")

    op.drop_index("ix_workflow_templates_enabled", table_name="workflow_templates")
    op.drop_index("ix_workflow_templates_name", table_name="workflow_templates")
    op.drop_index("ix_workflow_templates_workspace_id", table_name="workflow_templates")
    op.drop_table("workflow_templates")
