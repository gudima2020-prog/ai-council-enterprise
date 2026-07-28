"""Mission forecast calibration and autonomous learning loop.

Revision ID: 20260716_0030
Revises: 20260716_0029
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Iterable

from alembic import op
import sqlalchemy as sa


revision = "20260716_0030"
down_revision = "20260716_0029"
branch_labels = None
depends_on = None


def _indexes(table: str, columns: Iterable[str]) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column], unique=False)


def _drop_indexes(table: str, columns: Iterable[str]) -> None:
    for column in reversed(tuple(columns)):
        op.drop_index(f"ix_{table}_{column}", table_name=table)


def upgrade() -> None:
    op.create_table(
        "mission_learning_policies",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("learning_mode", sa.String(length=32), nullable=False),
        sa.Column("capture_outcomes_enabled", sa.Boolean(), nullable=False),
        sa.Column("auto_calibration_enabled", sa.Boolean(), nullable=False),
        sa.Column("auto_apply_enabled", sa.Boolean(), nullable=False),
        sa.Column("require_human_approval", sa.Boolean(), nullable=False),
        sa.Column("min_samples", sa.Integer(), nullable=False),
        sa.Column("calibration_window", sa.Integer(), nullable=False),
        sa.Column("max_probability_adjustment_percent", sa.Float(), nullable=False),
        sa.Column("max_multiplier_adjustment_percent", sa.Float(), nullable=False),
        sa.Column("min_improvement_percent", sa.Float(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "learning_mode IN ('manual', 'observe', 'adaptive')",
            name="ck_mission_learning_policies_mode",
        ),
        sa.CheckConstraint(
            "min_samples >= 1",
            name="ck_mission_learning_policies_min_samples",
        ),
        sa.CheckConstraint(
            "calibration_window >= 1",
            name="ck_mission_learning_policies_window",
        ),
        sa.CheckConstraint(
            "max_probability_adjustment_percent >= 0 AND "
            "max_probability_adjustment_percent <= 50",
            name="ck_mission_learning_policies_probability_adjustment",
        ),
        sa.CheckConstraint(
            "max_multiplier_adjustment_percent >= 0 AND "
            "max_multiplier_adjustment_percent <= 100",
            name="ck_mission_learning_policies_multiplier_adjustment",
        ),
        sa.CheckConstraint(
            "min_improvement_percent >= 0 AND min_improvement_percent <= 100",
            name="ck_mission_learning_policies_improvement",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mission_id", name="uq_mission_learning_policies_mission"
        ),
    )
    _indexes(
        "mission_learning_policies",
        ("workspace_id", "mission_id", "learning_mode", "created_at"),
    )

    op.create_table(
        "mission_forecast_outcomes",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("forecast_id", sa.String(length=64), nullable=False),
        sa.Column("source_event_id", sa.String(length=64), nullable=False),
        sa.Column("actual_status", sa.String(length=32), nullable=False),
        sa.Column("actual_success", sa.Boolean(), nullable=False),
        sa.Column("actual_completion", sa.Boolean(), nullable=False),
        sa.Column("predicted_success_percent", sa.Float(), nullable=False),
        sa.Column("predicted_completion_percent", sa.Float(), nullable=False),
        sa.Column("predicted_cost_usd", sa.Float(), nullable=False),
        sa.Column("predicted_remaining_cycles", sa.Float(), nullable=False),
        sa.Column(
            "predicted_p50_completion_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("actual_cost_usd", sa.Float(), nullable=False),
        sa.Column("actual_remaining_cycles", sa.Float(), nullable=False),
        sa.Column("actual_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("success_brier_score", sa.Float(), nullable=False),
        sa.Column("completion_brier_score", sa.Float(), nullable=False),
        sa.Column("success_absolute_error_percent", sa.Float(), nullable=False),
        sa.Column(
            "completion_absolute_error_percent", sa.Float(), nullable=False
        ),
        sa.Column("cost_absolute_error_usd", sa.Float(), nullable=False),
        sa.Column("cost_relative_error_percent", sa.Float(), nullable=True),
        sa.Column("cycles_absolute_error", sa.Float(), nullable=False),
        sa.Column("duration_absolute_error_seconds", sa.Float(), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "actual_status IN ('completed', 'failed', 'cancelled')",
            name="ck_mission_forecast_outcomes_status",
        ),
        sa.CheckConstraint(
            "predicted_success_percent >= 0 AND predicted_success_percent <= 100",
            name="ck_mission_forecast_outcomes_predicted_success",
        ),
        sa.CheckConstraint(
            "predicted_completion_percent >= 0 AND predicted_completion_percent <= 100",
            name="ck_mission_forecast_outcomes_predicted_completion",
        ),
        sa.CheckConstraint(
            "success_brier_score >= 0 AND success_brier_score <= 1",
            name="ck_mission_forecast_outcomes_success_brier",
        ),
        sa.CheckConstraint(
            "completion_brier_score >= 0 AND completion_brier_score <= 1",
            name="ck_mission_forecast_outcomes_completion_brier",
        ),
        sa.CheckConstraint(
            "actual_cost_usd >= 0",
            name="ck_mission_forecast_outcomes_cost",
        ),
        sa.CheckConstraint(
            "actual_remaining_cycles >= 0",
            name="ck_mission_forecast_outcomes_remaining_cycles",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["forecast_id"], ["mission_forecasts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "forecast_id", name="uq_mission_forecast_outcomes_forecast"
        ),
        sa.UniqueConstraint(
            "source_event_id",
            "forecast_id",
            name="uq_mission_forecast_outcomes_source_forecast",
        ),
    )
    _indexes(
        "mission_forecast_outcomes",
        (
            "workspace_id",
            "mission_id",
            "forecast_id",
            "source_event_id",
            "actual_status",
            "actual_completed_at",
            "resolved_at",
            "created_at",
        ),
    )

    op.create_table(
        "mission_forecast_calibrations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=True),
        sa.Column("policy_id", sa.String(length=64), nullable=True),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("method", sa.String(length=255), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("success_bias_percent", sa.Float(), nullable=False),
        sa.Column("completion_bias_percent", sa.Float(), nullable=False),
        sa.Column("cost_multiplier", sa.Float(), nullable=False),
        sa.Column("cycle_multiplier", sa.Float(), nullable=False),
        sa.Column("duration_multiplier", sa.Float(), nullable=False),
        sa.Column("confidence_multiplier", sa.Float(), nullable=False),
        sa.Column("baseline_score", sa.Float(), nullable=False),
        sa.Column("calibrated_score", sa.Float(), nullable=False),
        sa.Column("improvement_percent", sa.Float(), nullable=False),
        sa.Column("success_brier_score", sa.Float(), nullable=False),
        sa.Column("completion_brier_score", sa.Float(), nullable=False),
        sa.Column(
            "expected_calibration_error_percent", sa.Float(), nullable=False
        ),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scope_type IN ('mission', 'workspace')",
            name="ck_mission_forecast_calibrations_scope",
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'current', 'superseded', 'rejected')",
            name="ck_mission_forecast_calibrations_status",
        ),
        sa.CheckConstraint(
            "sample_count >= 0",
            name="ck_mission_forecast_calibrations_samples",
        ),
        sa.CheckConstraint(
            "success_bias_percent >= -50 AND success_bias_percent <= 50",
            name="ck_mission_forecast_calibrations_success_bias",
        ),
        sa.CheckConstraint(
            "completion_bias_percent >= -50 AND completion_bias_percent <= 50",
            name="ck_mission_forecast_calibrations_completion_bias",
        ),
        sa.CheckConstraint(
            "cost_multiplier >= 0.1 AND cost_multiplier <= 10",
            name="ck_mission_forecast_calibrations_cost_multiplier",
        ),
        sa.CheckConstraint(
            "cycle_multiplier >= 0.1 AND cycle_multiplier <= 10",
            name="ck_mission_forecast_calibrations_cycle_multiplier",
        ),
        sa.CheckConstraint(
            "duration_multiplier >= 0.1 AND duration_multiplier <= 10",
            name="ck_mission_forecast_calibrations_duration_multiplier",
        ),
        sa.CheckConstraint(
            "confidence_multiplier >= 0.1 AND confidence_multiplier <= 2",
            name="ck_mission_forecast_calibrations_confidence_multiplier",
        ),
        sa.CheckConstraint(
            "baseline_score >= 0 AND baseline_score <= 100",
            name="ck_mission_forecast_calibrations_baseline",
        ),
        sa.CheckConstraint(
            "calibrated_score >= 0 AND calibrated_score <= 100",
            name="ck_mission_forecast_calibrations_calibrated",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["mission_learning_policies.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes(
        "mission_forecast_calibrations",
        (
            "workspace_id",
            "mission_id",
            "policy_id",
            "scope_type",
            "scope_id",
            "status",
            "method",
            "actor_id",
            "applied_at",
            "created_at",
        ),
    )

    op.create_table(
        "mission_learning_runs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=True),
        sa.Column("calibration_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("baseline_score", sa.Float(), nullable=False),
        sa.Column("candidate_score", sa.Float(), nullable=False),
        sa.Column("improvement_percent", sa.Float(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source_summary_json", sa.JSON(), nullable=False),
        sa.Column("recommendations_json", sa.JSON(), nullable=False),
        sa.Column("applied_json", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('proposed', 'applied', 'rejected', 'failed')",
            name="ck_mission_learning_runs_status",
        ),
        sa.CheckConstraint(
            "sample_count >= 0",
            name="ck_mission_learning_runs_samples",
        ),
        sa.CheckConstraint(
            "baseline_score >= 0 AND baseline_score <= 100",
            name="ck_mission_learning_runs_baseline",
        ),
        sa.CheckConstraint(
            "candidate_score >= 0 AND candidate_score <= 100",
            name="ck_mission_learning_runs_candidate",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["mission_learning_policies.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["calibration_id"],
            ["mission_forecast_calibrations.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes(
        "mission_learning_runs",
        (
            "workspace_id",
            "mission_id",
            "policy_id",
            "calibration_id",
            "status",
            "actor_id",
            "scope_type",
            "created_at",
            "applied_at",
            "rejected_at",
        ),
    )

    op.create_table(
        "mission_learning_signals",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=True),
        sa.Column("source_event_id", sa.String(length=64), nullable=False),
        sa.Column("signal_type", sa.String(length=64), nullable=False),
        sa.Column("reward_percent", sa.Float(), nullable=False),
        sa.Column("confidence_percent", sa.Float(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "reward_percent >= 0 AND reward_percent <= 100",
            name="ck_mission_learning_signals_reward",
        ),
        sa.CheckConstraint(
            "confidence_percent >= 0 AND confidence_percent <= 100",
            name="ck_mission_learning_signals_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"], ["mission_cycles.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_event_id",
            "signal_type",
            name="uq_mission_learning_signals_source_type",
        ),
    )
    _indexes(
        "mission_learning_signals",
        (
            "workspace_id",
            "mission_id",
            "cycle_id",
            "source_event_id",
            "signal_type",
            "created_at",
        ),
    )


def downgrade() -> None:
    _drop_indexes(
        "mission_learning_signals",
        (
            "workspace_id",
            "mission_id",
            "cycle_id",
            "source_event_id",
            "signal_type",
            "created_at",
        ),
    )
    op.drop_table("mission_learning_signals")

    _drop_indexes(
        "mission_learning_runs",
        (
            "workspace_id",
            "mission_id",
            "policy_id",
            "calibration_id",
            "status",
            "actor_id",
            "scope_type",
            "created_at",
            "applied_at",
            "rejected_at",
        ),
    )
    op.drop_table("mission_learning_runs")

    _drop_indexes(
        "mission_forecast_calibrations",
        (
            "workspace_id",
            "mission_id",
            "policy_id",
            "scope_type",
            "scope_id",
            "status",
            "method",
            "actor_id",
            "applied_at",
            "created_at",
        ),
    )
    op.drop_table("mission_forecast_calibrations")

    _drop_indexes(
        "mission_forecast_outcomes",
        (
            "workspace_id",
            "mission_id",
            "forecast_id",
            "source_event_id",
            "actual_status",
            "actual_completed_at",
            "resolved_at",
            "created_at",
        ),
    )
    op.drop_table("mission_forecast_outcomes")

    _drop_indexes(
        "mission_learning_policies",
        ("workspace_id", "mission_id", "learning_mode", "created_at"),
    )
    op.drop_table("mission_learning_policies")
