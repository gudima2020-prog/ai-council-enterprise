"""Mission strategy portfolio and adaptive prioritization

Revision ID: 20260716_0024
Revises: 20260716_0023
Create Date: 2026-07-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260716_0024"
down_revision: Union[str, Sequence[str], None] = "20260716_0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _indexes(table: str, names: tuple[str, ...]) -> None:
    for name in names:
        op.create_index(f"ix_{table}_{name}", table, [name])


def _drop_indexes(table: str, names: tuple[str, ...]) -> None:
    for name in reversed(names):
        op.drop_index(f"ix_{table}_{name}", table_name=table)


def upgrade() -> None:
    op.create_table(
        "mission_strategy_policies",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("selection_mode", sa.String(length=32), nullable=False),
        sa.Column("require_human_selection", sa.Boolean(), nullable=False),
        sa.Column("auto_selection_enabled", sa.Boolean(), nullable=False),
        sa.Column("min_selection_score", sa.Float(), nullable=False),
        sa.Column("min_improvement_percent", sa.Float(), nullable=False),
        sa.Column("exploration_weight_percent", sa.Float(), nullable=False),
        sa.Column("performance_weight_percent", sa.Float(), nullable=False),
        sa.Column("cooldown_cycles", sa.Integer(), nullable=False),
        sa.Column("max_candidates", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "selection_mode IN ('manual', 'weighted', 'adaptive')",
            name="ck_mission_strategy_policies_mode",
        ),
        sa.CheckConstraint(
            "min_selection_score >= 0 AND min_selection_score <= 100",
            name="ck_mission_strategy_policies_min_score",
        ),
        sa.CheckConstraint(
            "min_improvement_percent >= 0 AND min_improvement_percent <= 100",
            name="ck_mission_strategy_policies_min_improvement",
        ),
        sa.CheckConstraint(
            "exploration_weight_percent >= 0 AND exploration_weight_percent <= 50",
            name="ck_mission_strategy_policies_exploration_weight",
        ),
        sa.CheckConstraint(
            "performance_weight_percent >= 0 AND performance_weight_percent <= 50",
            name="ck_mission_strategy_policies_performance_weight",
        ),
        sa.CheckConstraint(
            "cooldown_cycles >= 0",
            name="ck_mission_strategy_policies_cooldown",
        ),
        sa.CheckConstraint(
            "max_candidates >= 1",
            name="ck_mission_strategy_policies_max_candidates",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mission_id", name="uq_mission_strategy_policies_mission"
        ),
    )
    _indexes(
        "mission_strategy_policies",
        ("workspace_id", "mission_id", "selection_mode", "created_at"),
    )

    op.create_table(
        "mission_strategies",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("hypothesis_id", sa.String(length=64), nullable=True),
        sa.Column("strategy_key", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("strategy_hint", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("expected_value_percent", sa.Float(), nullable=False),
        sa.Column("success_probability_percent", sa.Float(), nullable=False),
        sa.Column("strategic_fit_percent", sa.Float(), nullable=False),
        sa.Column("feasibility_percent", sa.Float(), nullable=False),
        sa.Column("evidence_confidence_percent", sa.Float(), nullable=False),
        sa.Column("risk_percent", sa.Float(), nullable=False),
        sa.Column("cost_percent", sa.Float(), nullable=False),
        sa.Column("duration_percent", sa.Float(), nullable=False),
        sa.Column("base_score", sa.Float(), nullable=False),
        sa.Column("adaptive_score", sa.Float(), nullable=False),
        sa.Column("trial_count", sa.Integer(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("average_reward_percent", sa.Float(), nullable=False),
        sa.Column("constraints_json", sa.JSON(), nullable=False),
        sa.Column("tags_json", sa.JSON(), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_selected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'candidate', 'selected', 'paused', 'retired', 'rejected')",
            name="ck_mission_strategies_status",
        ),
        sa.CheckConstraint(
            "priority >= 0 AND priority <= 100",
            name="ck_mission_strategies_priority",
        ),
        sa.CheckConstraint(
            "expected_value_percent >= 0 AND expected_value_percent <= 100",
            name="ck_mission_strategies_expected_value",
        ),
        sa.CheckConstraint(
            "success_probability_percent >= 0 AND success_probability_percent <= 100",
            name="ck_mission_strategies_success_probability",
        ),
        sa.CheckConstraint(
            "strategic_fit_percent >= 0 AND strategic_fit_percent <= 100",
            name="ck_mission_strategies_strategic_fit",
        ),
        sa.CheckConstraint(
            "feasibility_percent >= 0 AND feasibility_percent <= 100",
            name="ck_mission_strategies_feasibility",
        ),
        sa.CheckConstraint(
            "evidence_confidence_percent >= 0 AND evidence_confidence_percent <= 100",
            name="ck_mission_strategies_evidence_confidence",
        ),
        sa.CheckConstraint(
            "risk_percent >= 0 AND risk_percent <= 100",
            name="ck_mission_strategies_risk",
        ),
        sa.CheckConstraint(
            "cost_percent >= 0 AND cost_percent <= 100",
            name="ck_mission_strategies_cost",
        ),
        sa.CheckConstraint(
            "duration_percent >= 0 AND duration_percent <= 100",
            name="ck_mission_strategies_duration",
        ),
        sa.CheckConstraint(
            "base_score >= 0 AND base_score <= 100",
            name="ck_mission_strategies_base_score",
        ),
        sa.CheckConstraint(
            "adaptive_score >= 0 AND adaptive_score <= 100",
            name="ck_mission_strategies_adaptive_score",
        ),
        sa.CheckConstraint(
            "average_reward_percent >= 0 AND average_reward_percent <= 100",
            name="ck_mission_strategies_average_reward",
        ),
        sa.CheckConstraint(
            "trial_count >= 0 AND success_count >= 0 AND failure_count >= 0",
            name="ck_mission_strategies_counts",
        ),
        sa.CheckConstraint(
            "version >= 1", name="ck_mission_strategies_version"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["hypothesis_id"], ["mission_hypotheses.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mission_id", "strategy_key", name="uq_mission_strategies_key"
        ),
    )
    _indexes(
        "mission_strategies",
        (
            "workspace_id",
            "mission_id",
            "hypothesis_id",
            "status",
            "priority",
            "base_score",
            "adaptive_score",
            "selected_at",
            "last_selected_at",
            "last_evaluated_at",
            "created_at",
        ),
    )

    op.create_table(
        "mission_strategy_evaluations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("evaluation_type", sa.String(length=32), nullable=False),
        sa.Column("expected_value_percent", sa.Float(), nullable=False),
        sa.Column("success_probability_percent", sa.Float(), nullable=False),
        sa.Column("strategic_fit_percent", sa.Float(), nullable=False),
        sa.Column("feasibility_percent", sa.Float(), nullable=False),
        sa.Column("evidence_confidence_percent", sa.Float(), nullable=False),
        sa.Column("risk_percent", sa.Float(), nullable=False),
        sa.Column("cost_percent", sa.Float(), nullable=False),
        sa.Column("duration_percent", sa.Float(), nullable=False),
        sa.Column("base_score", sa.Float(), nullable=False),
        sa.Column("adaptive_score", sa.Float(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("calculation_json", sa.JSON(), nullable=False),
        sa.Column("context_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "evaluation_type IN ('initial', 'manual', 'adaptive', 'feedback')",
            name="ck_mission_strategy_evaluations_type",
        ),
        sa.CheckConstraint(
            "base_score >= 0 AND base_score <= 100",
            name="ck_mission_strategy_evaluations_base_score",
        ),
        sa.CheckConstraint(
            "adaptive_score >= 0 AND adaptive_score <= 100",
            name="ck_mission_strategy_evaluations_adaptive_score",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["strategy_id"], ["mission_strategies.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes(
        "mission_strategy_evaluations",
        (
            "workspace_id",
            "mission_id",
            "strategy_id",
            "actor_id",
            "evaluation_type",
            "created_at",
        ),
    )

    op.create_table(
        "mission_strategy_selections",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("previous_strategy_id", sa.String(length=64), nullable=True),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("selection_mode", sa.String(length=32), nullable=False),
        sa.Column("score_at_selection", sa.Float(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "selection_mode IN ('manual', 'weighted', 'adaptive')",
            name="ck_mission_strategy_selections_mode",
        ),
        sa.CheckConstraint(
            "score_at_selection >= 0 AND score_at_selection <= 100",
            name="ck_mission_strategy_selections_score",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["strategy_id"], ["mission_strategies.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["previous_strategy_id"], ["mission_strategies.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes(
        "mission_strategy_selections",
        (
            "workspace_id",
            "mission_id",
            "strategy_id",
            "previous_strategy_id",
            "actor_id",
            "selection_mode",
            "created_at",
        ),
    )

    op.create_table(
        "mission_strategy_assignments",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("selection_mode", sa.String(length=32), nullable=False),
        sa.Column("selected_by", sa.String(length=255), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("score_at_assignment", sa.Float(), nullable=False),
        sa.Column("selection_reason", sa.Text(), nullable=False),
        sa.Column("reward_percent", sa.Float(), nullable=True),
        sa.Column("outcome_summary", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('planned', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_mission_strategy_assignments_status",
        ),
        sa.CheckConstraint(
            "selection_mode IN ('manual', 'weighted', 'adaptive')",
            name="ck_mission_strategy_assignments_mode",
        ),
        sa.CheckConstraint(
            "score_at_assignment >= 0 AND score_at_assignment <= 100",
            name="ck_mission_strategy_assignments_score",
        ),
        sa.CheckConstraint(
            "reward_percent IS NULL OR (reward_percent >= 0 AND reward_percent <= 100)",
            name="ck_mission_strategy_assignments_reward",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"], ["mission_cycles.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["strategy_id"], ["mission_strategies.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "cycle_id", name="uq_mission_strategy_assignments_cycle"
        ),
    )
    _indexes(
        "mission_strategy_assignments",
        (
            "workspace_id",
            "mission_id",
            "cycle_id",
            "strategy_id",
            "status",
            "selection_mode",
            "selected_by",
            "created_at",
        ),
    )


def downgrade() -> None:
    _drop_indexes(
        "mission_strategy_assignments",
        (
            "workspace_id",
            "mission_id",
            "cycle_id",
            "strategy_id",
            "status",
            "selection_mode",
            "selected_by",
            "created_at",
        ),
    )
    op.drop_table("mission_strategy_assignments")

    _drop_indexes(
        "mission_strategy_selections",
        (
            "workspace_id",
            "mission_id",
            "strategy_id",
            "previous_strategy_id",
            "actor_id",
            "selection_mode",
            "created_at",
        ),
    )
    op.drop_table("mission_strategy_selections")

    _drop_indexes(
        "mission_strategy_evaluations",
        (
            "workspace_id",
            "mission_id",
            "strategy_id",
            "actor_id",
            "evaluation_type",
            "created_at",
        ),
    )
    op.drop_table("mission_strategy_evaluations")

    _drop_indexes(
        "mission_strategies",
        (
            "workspace_id",
            "mission_id",
            "hypothesis_id",
            "status",
            "priority",
            "base_score",
            "adaptive_score",
            "selected_at",
            "last_selected_at",
            "last_evaluated_at",
            "created_at",
        ),
    )
    op.drop_table("mission_strategies")

    _drop_indexes(
        "mission_strategy_policies",
        ("workspace_id", "mission_id", "selection_mode", "created_at"),
    )
    op.drop_table("mission_strategy_policies")
