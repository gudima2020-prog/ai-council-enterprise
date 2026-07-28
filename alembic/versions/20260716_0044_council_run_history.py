"""P2-002 Council run persistence and Workspace history.

Revision ID: 20260716_0044
Revises: 20260716_0043
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260716_0044"
down_revision = "20260716_0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "council_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("replay_of_run_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("synthesizer_provider", sa.String(64), nullable=True),
        sa.Column("synthesizer_model", sa.String(255), nullable=True),
        sa.Column("synthesis_provider", sa.String(64), nullable=True),
        sa.Column("synthesis_model", sa.String(255), nullable=True),
        sa.Column("synthesis_requested_model", sa.String(255), nullable=True),
        sa.Column("synthesis_status", sa.String(32), nullable=True),
        sa.Column(
            "synthesis_fallback_used",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("synthesis_fallback_model", sa.String(255), nullable=True),
        sa.Column(
            "final_answer",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "consensus_json",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "disagreements_json",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "recommendations_json",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("successful_member_count", sa.Integer(), nullable=False),
        sa.Column(
            "duration_ms",
            sa.Float(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("correlation_id", sa.String(255), nullable=True),
        sa.Column("actor_id", sa.String(255), nullable=True),
        sa.Column("error_code", sa.String(96), nullable=True),
        sa.Column(
            "error_message",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "metadata_json",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["replay_of_run_id"],
            ["council_runs.id"],
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('completed', 'partial', 'failed')",
            name="ck_council_runs_status",
        ),
        sa.CheckConstraint(
            "mode IN ('universal', 'crypto', 'code', 'documents')",
            name="ck_council_runs_mode",
        ),
        sa.CheckConstraint(
            "member_count >= 2 AND member_count <= 6",
            name="ck_council_runs_member_count",
        ),
        sa.CheckConstraint(
            "successful_member_count >= 0 "
            "AND successful_member_count <= member_count",
            name="ck_council_runs_successful_count",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR "
            "(confidence >= 0 AND confidence <= 100)",
            name="ck_council_runs_confidence",
        ),
    )
    for name, columns in (
        ("ix_council_runs_workspace_id", ["workspace_id"]),
        ("ix_council_runs_replay_of_run_id", ["replay_of_run_id"]),
        ("ix_council_runs_status", ["status"]),
        ("ix_council_runs_mode", ["mode"]),
        ("ix_council_runs_correlation_id", ["correlation_id"]),
        ("ix_council_runs_actor_id", ["actor_id"]),
        ("ix_council_runs_started_at", ["started_at"]),
        ("ix_council_runs_created_at", ["created_at"]),
        (
            "ix_council_runs_workspace_started",
            ["workspace_id", "started_at"],
        ),
        (
            "ix_council_runs_workspace_status",
            ["workspace_id", "status", "started_at"],
        ),
    ):
        op.create_index(name, "council_runs", columns, unique=False)

    op.create_table(
        "council_run_members",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("requested_model", sa.String(255), nullable=True),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "fallback_used",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("fallback_model", sa.String(255), nullable=True),
        sa.Column(
            "answer",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
        sa.Column("error_code", sa.String(96), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["council_runs.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "run_id",
            "ordinal",
            name="uq_council_run_members_ordinal",
        ),
        sa.CheckConstraint(
            "status IN ('success', 'error')",
            name="ck_council_run_members_status",
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_council_run_members_input_tokens",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_council_run_members_output_tokens",
        ),
        sa.CheckConstraint(
            "total_tokens IS NULL OR total_tokens >= 0",
            name="ck_council_run_members_total_tokens",
        ),
    )
    op.create_index(
        "ix_council_run_members_run_id",
        "council_run_members",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        "ix_council_run_members_status",
        "council_run_members",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_council_run_members_status",
        table_name="council_run_members",
    )
    op.drop_index(
        "ix_council_run_members_run_id",
        table_name="council_run_members",
    )
    op.drop_table("council_run_members")

    for name in (
        "ix_council_runs_workspace_status",
        "ix_council_runs_workspace_started",
        "ix_council_runs_created_at",
        "ix_council_runs_started_at",
        "ix_council_runs_actor_id",
        "ix_council_runs_correlation_id",
        "ix_council_runs_mode",
        "ix_council_runs_status",
        "ix_council_runs_replay_of_run_id",
        "ix_council_runs_workspace_id",
    ):
        op.drop_index(name, table_name="council_runs")
    op.drop_table("council_runs")
