"""Execution observability metrics, SLO policies and breaches

Revision ID: 20260715_0018
Revises: 20260715_0017
Create Date: 2026-07-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0018"
down_revision: Union[str, Sequence[str], None] = "20260715_0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "execution_slo_policies",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("scope_key", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("window_minutes", sa.Integer(), nullable=False),
        sa.Column("evaluation_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("min_sample_size", sa.Integer(), nullable=False),
        sa.Column("target_plan_success_rate_pct", sa.Float(), nullable=False),
        sa.Column("target_step_success_rate_pct", sa.Float(), nullable=False),
        sa.Column("max_p95_plan_duration_ms", sa.Float(), nullable=False),
        sa.Column("max_p95_step_duration_ms", sa.Float(), nullable=False),
        sa.Column("max_tool_failure_rate_pct", sa.Float(), nullable=False),
        sa.Column("max_open_critical_incidents", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scope_key",
            name="uq_execution_slo_policies_scope_key",
        ),
    )
    for name in ("scope_key", "workspace_id", "enabled", "created_at"):
        op.create_index(
            f"ix_execution_slo_policies_{name}",
            "execution_slo_policies",
            [name],
        )

    op.create_table(
        "execution_metric_snapshots",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("scope_key", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for name in (
        "scope_key",
        "workspace_id",
        "window_started_at",
        "window_ended_at",
        "created_at",
    ):
        op.create_index(
            f"ix_execution_metric_snapshots_{name}",
            "execution_metric_snapshots",
            [name],
        )

    op.create_table(
        "execution_slo_breaches",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=True),
        sa.Column("scope_key", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("metric_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("comparison", sa.String(length=16), nullable=False),
        sa.Column("dedupe_key", sa.String(length=512), nullable=False),
        sa.Column("target_value", sa.Float(), nullable=False),
        sa.Column("actual_value", sa.Float(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("first_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=255), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('open', 'resolved', 'dismissed')",
            name="ck_execution_slo_breaches_status",
        ),
        sa.CheckConstraint(
            "severity IN ('warning', 'high', 'critical')",
            name="ck_execution_slo_breaches_severity",
        ),
        sa.CheckConstraint(
            "comparison IN ('minimum', 'maximum')",
            name="ck_execution_slo_breaches_comparison",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["execution_slo_policies.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dedupe_key",
            name="uq_execution_slo_breaches_dedupe_key",
        ),
    )
    for name in (
        "policy_id",
        "scope_key",
        "workspace_id",
        "metric_name",
        "status",
        "severity",
        "first_detected_at",
        "created_at",
    ):
        op.create_index(
            f"ix_execution_slo_breaches_{name}",
            "execution_slo_breaches",
            [name],
        )


def downgrade() -> None:
    for name in reversed(
        (
            "policy_id",
            "scope_key",
            "workspace_id",
            "metric_name",
            "status",
            "severity",
            "first_detected_at",
            "created_at",
        )
    ):
        op.drop_index(
            f"ix_execution_slo_breaches_{name}",
            table_name="execution_slo_breaches",
        )
    op.drop_table("execution_slo_breaches")

    for name in reversed(
        (
            "scope_key",
            "workspace_id",
            "window_started_at",
            "window_ended_at",
            "created_at",
        )
    ):
        op.drop_index(
            f"ix_execution_metric_snapshots_{name}",
            table_name="execution_metric_snapshots",
        )
    op.drop_table("execution_metric_snapshots")

    for name in reversed(("scope_key", "workspace_id", "enabled", "created_at")):
        op.drop_index(
            f"ix_execution_slo_policies_{name}",
            table_name="execution_slo_policies",
        )
    op.drop_table("execution_slo_policies")
