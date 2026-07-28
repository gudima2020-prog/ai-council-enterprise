"""P2-004 Council presets and cost control.

Revision ID: 20260724_0046
Revises: 20260724_0045
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260724_0046"
down_revision = "20260724_0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("council_runs") as batch_op:
        batch_op.add_column(sa.Column("estimated_cost_usd", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("cost_estimate_status", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("cost_approval_id", sa.String(length=64), nullable=True))
        batch_op.create_index("ix_council_runs_cost_approval_id", ["cost_approval_id"], unique=False)

    op.create_table(
        "council_presets",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("members_json", sa.JSON(), nullable=False),
        sa.Column("synthesizer_provider", sa.String(length=64), nullable=True),
        sa.Column("synthesizer_model", sa.String(length=255), nullable=True),
        sa.Column("member_timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "mode IN ('universal', 'crypto', 'code', 'documents')",
            name="ck_council_presets_mode",
        ),
        sa.CheckConstraint(
            "member_timeout_seconds >= 5 AND member_timeout_seconds <= 600",
            name="ck_council_presets_timeout",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "name", name="uq_council_presets_workspace_name"),
    )
    op.create_index("ix_council_presets_workspace_id", "council_presets", ["workspace_id"], unique=False)
    op.create_index("ix_council_presets_created_at", "council_presets", ["created_at"], unique=False)

    op.create_table(
        "council_cost_approvals",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("token", sa.String(length=96), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column("estimate_status", sa.String(length=16), nullable=False),
        sa.Column("reasons_json", sa.JSON(), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("decision IN ('approved')", name="ck_council_cost_approvals_decision"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token"),
    )
    op.create_index("ix_council_cost_approvals_token", "council_cost_approvals", ["token"], unique=True)
    op.create_index("ix_council_cost_approvals_workspace_id", "council_cost_approvals", ["workspace_id"], unique=False)
    op.create_index("ix_council_cost_approvals_fingerprint", "council_cost_approvals", ["fingerprint"], unique=False)
    op.create_index("ix_council_cost_approvals_created_at", "council_cost_approvals", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_council_cost_approvals_created_at", table_name="council_cost_approvals")
    op.drop_index("ix_council_cost_approvals_fingerprint", table_name="council_cost_approvals")
    op.drop_index("ix_council_cost_approvals_workspace_id", table_name="council_cost_approvals")
    op.drop_index("ix_council_cost_approvals_token", table_name="council_cost_approvals")
    op.drop_table("council_cost_approvals")
    op.drop_index("ix_council_presets_created_at", table_name="council_presets")
    op.drop_index("ix_council_presets_workspace_id", table_name="council_presets")
    op.drop_table("council_presets")

    with op.batch_alter_table("council_runs") as batch_op:
        batch_op.drop_index("ix_council_runs_cost_approval_id")
        batch_op.drop_column("cost_approval_id")
        batch_op.drop_column("cost_estimate_status")
        batch_op.drop_column("estimated_cost_usd")
