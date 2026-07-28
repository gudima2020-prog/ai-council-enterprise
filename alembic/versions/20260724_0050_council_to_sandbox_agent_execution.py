"""P2-008 Council-to-Sandbox capability-limited agent execution.

Revision ID: 20260724_0050
Revises: 20260724_0049
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260724_0050"
down_revision = "20260724_0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "code_sandbox_agent_runs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(length=64),
            sa.ForeignKey("code_sandbox_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.String(length=64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "council_run_id",
            sa.String(length=64),
            sa.ForeignKey("council_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="created"),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("gateway_request_id", sa.String(length=64), nullable=True),
        sa.Column("task_sha256", sa.String(length=64), nullable=False),
        sa.Column("task_preview", sa.Text(), nullable=False, server_default=""),
        sa.Column("context_paths_json", sa.JSON(), nullable=False),
        sa.Column("writable_paths_json", sa.JSON(), nullable=False),
        sa.Column("operations_json", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("actual_cost_usd", sa.Float(), nullable=True),
        sa.Column("cost_status", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('created', 'running', 'completed', 'blocked', 'failed')",
            name="ck_code_sandbox_agent_runs_status",
        ),
        sa.CheckConstraint(
            "cost_status IN ('known', 'unknown')",
            name="ck_code_sandbox_agent_runs_cost_status",
        ),
    )
    op.create_index("ix_code_sandbox_agent_runs_session_id", "code_sandbox_agent_runs", ["session_id"])
    op.create_index("ix_code_sandbox_agent_runs_workspace_id", "code_sandbox_agent_runs", ["workspace_id"])
    op.create_index("ix_code_sandbox_agent_runs_council_run_id", "code_sandbox_agent_runs", ["council_run_id"])
    op.create_index("ix_code_sandbox_agent_runs_status", "code_sandbox_agent_runs", ["status"])
    op.create_index("ix_code_sandbox_agent_runs_created_at", "code_sandbox_agent_runs", ["created_at"])


def downgrade() -> None:
    op.drop_table("code_sandbox_agent_runs")
