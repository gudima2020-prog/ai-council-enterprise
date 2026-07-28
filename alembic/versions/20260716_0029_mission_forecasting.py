"""Mission outcome forecasts, scenarios and Workspace what-if simulations.

Revision ID: 20260716_0029
Revises: 20260716_0028
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Iterable

from alembic import op
import sqlalchemy as sa


revision = "20260716_0029"
down_revision = "20260716_0028"
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
        "mission_forecast_policies",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("forecast_mode", sa.String(length=32), nullable=False),
        sa.Column("horizon_cycles", sa.Integer(), nullable=False),
        sa.Column("min_samples", sa.Integer(), nullable=False),
        sa.Column("stale_after_seconds", sa.Integer(), nullable=False),
        sa.Column("auto_refresh_enabled", sa.Boolean(), nullable=False),
        sa.Column("require_human_approval", sa.Boolean(), nullable=False),
        sa.Column("allow_auto_scenario_selection", sa.Boolean(), nullable=False),
        sa.Column("confidence_threshold_percent", sa.Float(), nullable=False),
        sa.Column("max_scenarios", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "forecast_mode IN ('manual', 'heuristic', 'adaptive')",
            name="ck_mission_forecast_policies_mode",
        ),
        sa.CheckConstraint(
            "horizon_cycles >= 1",
            name="ck_mission_forecast_policies_horizon",
        ),
        sa.CheckConstraint(
            "min_samples >= 1",
            name="ck_mission_forecast_policies_min_samples",
        ),
        sa.CheckConstraint(
            "stale_after_seconds >= 60",
            name="ck_mission_forecast_policies_stale",
        ),
        sa.CheckConstraint(
            "confidence_threshold_percent >= 0 AND confidence_threshold_percent <= 100",
            name="ck_mission_forecast_policies_confidence",
        ),
        sa.CheckConstraint(
            "max_scenarios >= 1",
            name="ck_mission_forecast_policies_max_scenarios",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mission_id", name="uq_mission_forecast_policies_mission"
        ),
    )
    _indexes(
        "mission_forecast_policies",
        ("workspace_id", "mission_id", "forecast_mode", "created_at"),
    )

    op.create_table(
        "mission_forecasts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("method", sa.String(length=255), nullable=False),
        sa.Column("horizon_cycles", sa.Integer(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("success_probability_percent", sa.Float(), nullable=False),
        sa.Column("completion_probability_percent", sa.Float(), nullable=False),
        sa.Column("expected_progress_percent", sa.Float(), nullable=False),
        sa.Column("confidence_percent", sa.Float(), nullable=False),
        sa.Column("risk_score", sa.Float(), nullable=False),
        sa.Column("expected_remaining_cycles", sa.Float(), nullable=False),
        sa.Column("expected_cost_usd", sa.Float(), nullable=False),
        sa.Column("p50_completion_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("p90_completion_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("assumptions_json", sa.JSON(), nullable=False),
        sa.Column("drivers_json", sa.JSON(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('current', 'superseded', 'invalidated')",
            name="ck_mission_forecasts_status",
        ),
        sa.CheckConstraint(
            "horizon_cycles >= 1",
            name="ck_mission_forecasts_horizon",
        ),
        sa.CheckConstraint(
            "sample_count >= 0",
            name="ck_mission_forecasts_sample_count",
        ),
        sa.CheckConstraint(
            "success_probability_percent >= 0 AND success_probability_percent <= 100",
            name="ck_mission_forecasts_success_probability",
        ),
        sa.CheckConstraint(
            "completion_probability_percent >= 0 AND completion_probability_percent <= 100",
            name="ck_mission_forecasts_completion_probability",
        ),
        sa.CheckConstraint(
            "expected_progress_percent >= 0 AND expected_progress_percent <= 100",
            name="ck_mission_forecasts_expected_progress",
        ),
        sa.CheckConstraint(
            "confidence_percent >= 0 AND confidence_percent <= 100",
            name="ck_mission_forecasts_confidence",
        ),
        sa.CheckConstraint(
            "risk_score >= 0 AND risk_score <= 100",
            name="ck_mission_forecasts_risk",
        ),
        sa.CheckConstraint(
            "expected_remaining_cycles >= 0",
            name="ck_mission_forecasts_remaining_cycles",
        ),
        sa.CheckConstraint(
            "expected_cost_usd >= 0",
            name="ck_mission_forecasts_cost",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["mission_forecast_policies.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes(
        "mission_forecasts",
        (
            "workspace_id",
            "mission_id",
            "policy_id",
            "status",
            "method",
            "success_probability_percent",
            "completion_probability_percent",
            "confidence_percent",
            "risk_score",
            "p50_completion_at",
            "p90_completion_at",
            "actor_id",
            "generated_at",
            "created_at",
        ),
    )

    op.create_table(
        "mission_scenarios",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("baseline_forecast_id", sa.String(length=64), nullable=True),
        sa.Column("scenario_key", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("scenario_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("overrides_json", sa.JSON(), nullable=False),
        sa.Column("assumptions_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("selected_by", sa.String(length=255), nullable=True),
        sa.Column("selection_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scenario_type IN ('baseline', 'optimistic', 'pessimistic', 'custom')",
            name="ck_mission_scenarios_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'evaluated', 'selected', 'archived')",
            name="ck_mission_scenarios_status",
        ),
        sa.CheckConstraint(
            "score >= 0 AND score <= 100",
            name="ck_mission_scenarios_score",
        ),
        sa.CheckConstraint("version >= 1", name="ck_mission_scenarios_version"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["baseline_forecast_id"], ["mission_forecasts.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mission_id", "scenario_key", name="uq_mission_scenarios_key"
        ),
    )
    _indexes(
        "mission_scenarios",
        (
            "workspace_id",
            "mission_id",
            "baseline_forecast_id",
            "scenario_type",
            "status",
            "score",
            "selected_by",
            "evaluated_at",
            "selected_at",
            "created_at",
        ),
    )

    op.create_table(
        "workspace_portfolio_simulations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("mission_count", sa.Integer(), nullable=False),
        sa.Column("selected_count", sa.Integer(), nullable=False),
        sa.Column("portfolio_score", sa.Float(), nullable=False),
        sa.Column("expected_cost_usd", sa.Float(), nullable=False),
        sa.Column("expected_successful_missions", sa.Float(), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("assumptions_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('completed', 'failed')",
            name="ck_workspace_portfolio_simulations_status",
        ),
        sa.CheckConstraint(
            "mission_count >= 0",
            name="ck_workspace_portfolio_simulations_mission_count",
        ),
        sa.CheckConstraint(
            "selected_count >= 0",
            name="ck_workspace_portfolio_simulations_selected_count",
        ),
        sa.CheckConstraint(
            "portfolio_score >= 0 AND portfolio_score <= 100",
            name="ck_workspace_portfolio_simulations_score",
        ),
        sa.CheckConstraint(
            "expected_cost_usd >= 0",
            name="ck_workspace_portfolio_simulations_cost",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes(
        "workspace_portfolio_simulations",
        (
            "workspace_id",
            "status",
            "actor_id",
            "portfolio_score",
            "created_at",
            "completed_at",
        ),
    )


def downgrade() -> None:
    _drop_indexes(
        "workspace_portfolio_simulations",
        (
            "workspace_id",
            "status",
            "actor_id",
            "portfolio_score",
            "created_at",
            "completed_at",
        ),
    )
    op.drop_table("workspace_portfolio_simulations")

    _drop_indexes(
        "mission_scenarios",
        (
            "workspace_id",
            "mission_id",
            "baseline_forecast_id",
            "scenario_type",
            "status",
            "score",
            "selected_by",
            "evaluated_at",
            "selected_at",
            "created_at",
        ),
    )
    op.drop_table("mission_scenarios")

    _drop_indexes(
        "mission_forecasts",
        (
            "workspace_id",
            "mission_id",
            "policy_id",
            "status",
            "method",
            "success_probability_percent",
            "completion_probability_percent",
            "confidence_percent",
            "risk_score",
            "p50_completion_at",
            "p90_completion_at",
            "actor_id",
            "generated_at",
            "created_at",
        ),
    )
    op.drop_table("mission_forecasts")

    _drop_indexes(
        "mission_forecast_policies",
        ("workspace_id", "mission_id", "forecast_mode", "created_at"),
    )
    op.drop_table("mission_forecast_policies")
