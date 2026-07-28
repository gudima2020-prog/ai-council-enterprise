"""P2-007 Git-isolated Code Sandbox and verifiable patch workflow.

Revision ID: 20260724_0049
Revises: 20260724_0048
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260724_0049"
down_revision = "20260724_0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "code_sandbox_sessions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("workspace_id", sa.String(length=64), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True),
        sa.Column("repo_path", sa.Text(), nullable=False),
        sa.Column("worktree_path", sa.Text(), nullable=False),
        sa.Column("base_ref", sa.String(length=255), nullable=False),
        sa.Column("base_commit", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("risk_level", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column("approval_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("verification_status", sa.String(length=16), nullable=False, server_default="not_run"),
        sa.Column("patch_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("patch_sha256", sa.String(length=64), nullable=True),
        sa.Column("patch_path", sa.Text(), nullable=True),
        sa.Column("files_changed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("insertions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deletions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changed_paths_json", sa.JSON(), nullable=False),
        sa.Column("protected_paths_json", sa.JSON(), nullable=False),
        sa.Column("blocked_paths_json", sa.JSON(), nullable=False),
        sa.Column("verification_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('active', 'inspected', 'verified', 'applied', 'closed', 'error')", name="ck_code_sandbox_sessions_status"),
        sa.CheckConstraint("risk_level IN ('none', 'low', 'medium', 'high', 'critical')", name="ck_code_sandbox_sessions_risk_level"),
        sa.CheckConstraint("verification_status IN ('not_run', 'passed', 'failed')", name="ck_code_sandbox_sessions_verification_status"),
    )
    op.create_index("ix_code_sandbox_sessions_workspace_id", "code_sandbox_sessions", ["workspace_id"])
    op.create_index("ix_code_sandbox_sessions_base_commit", "code_sandbox_sessions", ["base_commit"])
    op.create_index("ix_code_sandbox_sessions_status", "code_sandbox_sessions", ["status"])
    op.create_index("ix_code_sandbox_sessions_risk_level", "code_sandbox_sessions", ["risk_level"])
    op.create_index("ix_code_sandbox_sessions_patch_fingerprint", "code_sandbox_sessions", ["patch_fingerprint"])
    op.create_index("ix_code_sandbox_sessions_created_at", "code_sandbox_sessions", ["created_at"])

    op.create_table(
        "code_sandbox_approvals",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("session_id", sa.String(length=64), sa.ForeignKey("code_sandbox_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'used', 'expired', 'revoked')", name="ck_code_sandbox_approvals_status"),
    )
    op.create_index("ix_code_sandbox_approvals_session_id", "code_sandbox_approvals", ["session_id"])
    op.create_index("ix_code_sandbox_approvals_workspace_id", "code_sandbox_approvals", ["workspace_id"])
    op.create_index("ix_code_sandbox_approvals_fingerprint", "code_sandbox_approvals", ["fingerprint"])
    op.create_index("ix_code_sandbox_approvals_status", "code_sandbox_approvals", ["status"])
    op.create_index("ix_code_sandbox_approvals_expires_at", "code_sandbox_approvals", ["expires_at"])


def downgrade() -> None:
    op.drop_table("code_sandbox_approvals")
    op.drop_table("code_sandbox_sessions")
