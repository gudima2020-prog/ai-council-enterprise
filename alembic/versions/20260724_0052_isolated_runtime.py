"""P2-010 Docker isolated runtime audit trail.

Revision ID: 20260724_0052
Revises: 20260724_0051
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260724_0052"
down_revision = "20260724_0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "code_sandbox_runtime_runs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("session_id", sa.String(length=64), sa.ForeignKey("code_sandbox_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True),
        sa.Column("profile", sa.String(length=32), nullable=False),
        sa.Column("backend", sa.String(length=16), nullable=False, server_default="docker"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("image", sa.String(length=255), nullable=False),
        sa.Column("image_id", sa.Text(), nullable=True),
        sa.Column("network_mode", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column("cpu_limit", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("memory_mb", sa.Integer(), nullable=False, server_default="1024"),
        sa.Column("pids_limit", sa.Integer(), nullable=False, server_default="128"),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("timed_out", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("output", sa.Text(), nullable=False, server_default=""),
        sa.Column("artifact_paths_json", sa.JSON(), nullable=False),
        sa.Column("artifact_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('running', 'passed', 'failed', 'timeout')", name="ck_code_sandbox_runtime_runs_status"),
        sa.CheckConstraint("backend IN ('docker')", name="ck_code_sandbox_runtime_runs_backend"),
    )
    op.create_index("ix_code_sandbox_runtime_runs_session_id", "code_sandbox_runtime_runs", ["session_id"])
    op.create_index("ix_code_sandbox_runtime_runs_workspace_id", "code_sandbox_runtime_runs", ["workspace_id"])
    op.create_index("ix_code_sandbox_runtime_runs_profile", "code_sandbox_runtime_runs", ["profile"])
    op.create_index("ix_code_sandbox_runtime_runs_status", "code_sandbox_runtime_runs", ["status"])
    op.create_index("ix_code_sandbox_runtime_runs_created_at", "code_sandbox_runtime_runs", ["created_at"])


def downgrade() -> None:
    op.drop_table("code_sandbox_runtime_runs")
