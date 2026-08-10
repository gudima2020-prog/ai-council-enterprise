"""P3-002.1b agent policy profile persistence and selection.

Revision ID: 20260807_0058
Revises: 20260806_0057
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260807_0058"
down_revision = "20260806_0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_policy_profile_versions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("profile_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("profile_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "profile_id",
            "version",
            name="uq_agent_policy_profile_versions_workspace_profile_version",
        ),
    )
    for column in (
        "workspace_id",
        "profile_id",
        "version",
        "profile_fingerprint",
        "created_by",
        "created_at",
    ):
        op.create_index(
            f"ix_agent_policy_profile_versions_{column}",
            "agent_policy_profile_versions",
            [column],
        )

    op.create_table(
        "agent_policy_profile_selections",
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("profile_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("profile_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source IN ('built_in', 'custom')",
            name="ck_agent_policy_profile_selections_source",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("workspace_id"),
    )
    for column in (
        "profile_id",
        "profile_fingerprint",
        "updated_by",
        "updated_at",
    ):
        op.create_index(
            f"ix_agent_policy_profile_selections_{column}",
            "agent_policy_profile_selections",
            [column],
        )


def downgrade() -> None:
    op.drop_table("agent_policy_profile_selections")
    op.drop_table("agent_policy_profile_versions")
