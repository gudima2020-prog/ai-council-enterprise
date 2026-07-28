"""Mission dependencies and portfolio coordination.

Revision ID: 20260716_0027
Revises: 20260716_0026
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260716_0027"
down_revision: Union[str, Sequence[str], None] = "20260716_0026"
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
        "mission_dependencies",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("depends_on_mission_id", sa.String(length=64), nullable=False),
        sa.Column("dependency_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("required_statuses_json", sa.JSON(), nullable=False),
        sa.Column("allow_failed", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("satisfied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("waived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("waived_by", sa.String(length=255), nullable=True),
        sa.Column("waiver_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("dependency_type IN ('hard', 'soft', 'informational')", name="ck_mission_dependencies_type"),
        sa.CheckConstraint("status IN ('pending', 'satisfied', 'waived', 'failed')", name="ck_mission_dependencies_status"),
        sa.CheckConstraint("priority >= 0 AND priority <= 100", name="ck_mission_dependencies_priority"),
        sa.CheckConstraint("mission_id != depends_on_mission_id", name="ck_mission_dependencies_not_self"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["depends_on_mission_id"], ["workspace_missions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mission_id", "depends_on_mission_id", name="uq_mission_dependencies_pair"),
    )
    _indexes(
        "mission_dependencies",
        (
            "workspace_id",
            "mission_id",
            "depends_on_mission_id",
            "dependency_type",
            "status",
            "priority",
            "satisfied_at",
            "waived_at",
            "waived_by",
            "created_at",
        ),
    )

    op.create_table(
        "mission_portfolio_policies",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("prioritization_mode", sa.String(length=32), nullable=False),
        sa.Column("require_human_approval", sa.Boolean(), nullable=False),
        sa.Column("auto_rebalance_enabled", sa.Boolean(), nullable=False),
        sa.Column("enforce_cycle_admission", sa.Boolean(), nullable=False),
        sa.Column("max_parallel_missions", sa.Integer(), nullable=False),
        sa.Column("min_selection_score", sa.Float(), nullable=False),
        sa.Column("rebalance_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("priority_weight", sa.Float(), nullable=False),
        sa.Column("progress_weight", sa.Float(), nullable=False),
        sa.Column("deadline_weight", sa.Float(), nullable=False),
        sa.Column("dependency_weight", sa.Float(), nullable=False),
        sa.Column("strategy_weight", sa.Float(), nullable=False),
        sa.Column("risk_penalty_weight", sa.Float(), nullable=False),
        sa.Column("last_rebalanced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("prioritization_mode IN ('manual', 'weighted', 'adaptive')", name="ck_mission_portfolio_policies_mode"),
        sa.CheckConstraint("max_parallel_missions >= 1", name="ck_mission_portfolio_policies_parallel"),
        sa.CheckConstraint("min_selection_score >= 0 AND min_selection_score <= 100", name="ck_mission_portfolio_policies_min_score"),
        sa.CheckConstraint("rebalance_interval_seconds >= 30", name="ck_mission_portfolio_policies_interval"),
        sa.CheckConstraint("priority_weight >= 0 AND priority_weight <= 100", name="ck_mission_portfolio_policies_priority_weight"),
        sa.CheckConstraint("progress_weight >= 0 AND progress_weight <= 100", name="ck_mission_portfolio_policies_progress_weight"),
        sa.CheckConstraint("deadline_weight >= 0 AND deadline_weight <= 100", name="ck_mission_portfolio_policies_deadline_weight"),
        sa.CheckConstraint("dependency_weight >= 0 AND dependency_weight <= 100", name="ck_mission_portfolio_policies_dependency_weight"),
        sa.CheckConstraint("strategy_weight >= 0 AND strategy_weight <= 100", name="ck_mission_portfolio_policies_strategy_weight"),
        sa.CheckConstraint("risk_penalty_weight >= 0 AND risk_penalty_weight <= 100", name="ck_mission_portfolio_policies_risk_weight"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", name="uq_mission_portfolio_policies_workspace"),
    )
    _indexes(
        "mission_portfolio_policies",
        ("workspace_id", "prioritization_mode", "last_rebalanced_at", "created_at"),
    )

    op.create_table(
        "mission_portfolio_evaluations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=True),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("components_json", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("context_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("decision IN ('select', 'keep', 'defer', 'block')", name="ck_mission_portfolio_evaluations_decision"),
        sa.CheckConstraint("score >= 0 AND score <= 100", name="ck_mission_portfolio_evaluations_score"),
        sa.CheckConstraint("rank >= 1", name="ck_mission_portfolio_evaluations_rank"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["policy_id"], ["mission_portfolio_policies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes(
        "mission_portfolio_evaluations",
        ("workspace_id", "mission_id", "policy_id", "actor_id", "score", "rank", "decision", "created_at"),
    )

    op.create_table(
        "mission_portfolio_assignments",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("manual_override", sa.Boolean(), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('selected', 'deferred', 'blocked', 'released')", name="ck_mission_portfolio_assignments_status"),
        sa.CheckConstraint("score >= 0 AND score <= 100", name="ck_mission_portfolio_assignments_score"),
        sa.CheckConstraint("rank >= 0", name="ck_mission_portfolio_assignments_rank"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["policy_id"], ["mission_portfolio_policies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mission_id", name="uq_mission_portfolio_assignments_mission"),
    )
    _indexes(
        "mission_portfolio_assignments",
        ("workspace_id", "mission_id", "policy_id", "status", "score", "rank", "actor_id", "assigned_at", "released_at"),
    )


def downgrade() -> None:
    _drop_indexes(
        "mission_portfolio_assignments",
        ("workspace_id", "mission_id", "policy_id", "status", "score", "rank", "actor_id", "assigned_at", "released_at"),
    )
    op.drop_table("mission_portfolio_assignments")
    _drop_indexes(
        "mission_portfolio_evaluations",
        ("workspace_id", "mission_id", "policy_id", "actor_id", "score", "rank", "decision", "created_at"),
    )
    op.drop_table("mission_portfolio_evaluations")
    _drop_indexes(
        "mission_portfolio_policies",
        ("workspace_id", "prioritization_mode", "last_rebalanced_at", "created_at"),
    )
    op.drop_table("mission_portfolio_policies")
    _drop_indexes(
        "mission_dependencies",
        (
            "workspace_id",
            "mission_id",
            "depends_on_mission_id",
            "dependency_type",
            "status",
            "priority",
            "satisfied_at",
            "waived_at",
            "waived_by",
            "created_at",
        ),
    )
    op.drop_table("mission_dependencies")
