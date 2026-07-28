"""Mission resource allocation, budgets and adaptive capacity planning

Revision ID: 20260716_0025
Revises: 20260716_0024
Create Date: 2026-07-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260716_0025"
down_revision: Union[str, Sequence[str], None] = "20260716_0024"
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
        "mission_resource_policies",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("allocation_mode", sa.String(length=32), nullable=False),
        sa.Column("require_human_approval", sa.Boolean(), nullable=False),
        sa.Column("auto_allocation_enabled", sa.Boolean(), nullable=False),
        sa.Column("auto_rebalance_enabled", sa.Boolean(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("total_budget_usd", sa.Float(), nullable=False),
        sa.Column("default_cycle_budget_usd", sa.Float(), nullable=False),
        sa.Column("max_cycle_budget_usd", sa.Float(), nullable=False),
        sa.Column("reserve_percent", sa.Float(), nullable=False),
        sa.Column("max_parallel_cycles", sa.Integer(), nullable=False),
        sa.Column("agent_slots", sa.Integer(), nullable=False),
        sa.Column("tool_slots", sa.Integer(), nullable=False),
        sa.Column("compute_units", sa.Float(), nullable=False),
        sa.Column("planning_horizon_cycles", sa.Integer(), nullable=False),
        sa.Column("min_rebalance_improvement_percent", sa.Float(), nullable=False),
        sa.Column("overrun_tolerance_percent", sa.Float(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "allocation_mode IN ('manual', 'balanced', 'adaptive')",
            name="ck_mission_resource_policies_mode",
        ),
        sa.CheckConstraint(
            "total_budget_usd >= 0",
            name="ck_mission_resource_policies_total_budget",
        ),
        sa.CheckConstraint(
            "default_cycle_budget_usd >= 0",
            name="ck_mission_resource_policies_default_cycle_budget",
        ),
        sa.CheckConstraint(
            "max_cycle_budget_usd >= 0",
            name="ck_mission_resource_policies_max_cycle_budget",
        ),
        sa.CheckConstraint(
            "reserve_percent >= 0 AND reserve_percent <= 100",
            name="ck_mission_resource_policies_reserve_percent",
        ),
        sa.CheckConstraint(
            "max_parallel_cycles >= 1",
            name="ck_mission_resource_policies_parallel_cycles",
        ),
        sa.CheckConstraint(
            "agent_slots >= 1",
            name="ck_mission_resource_policies_agent_slots",
        ),
        sa.CheckConstraint(
            "tool_slots >= 1",
            name="ck_mission_resource_policies_tool_slots",
        ),
        sa.CheckConstraint(
            "compute_units >= 0",
            name="ck_mission_resource_policies_compute_units",
        ),
        sa.CheckConstraint(
            "planning_horizon_cycles >= 1",
            name="ck_mission_resource_policies_horizon",
        ),
        sa.CheckConstraint(
            "min_rebalance_improvement_percent >= 0 AND min_rebalance_improvement_percent <= 100",
            name="ck_mission_resource_policies_rebalance_improvement",
        ),
        sa.CheckConstraint(
            "overrun_tolerance_percent >= 0 AND overrun_tolerance_percent <= 100",
            name="ck_mission_resource_policies_overrun_tolerance",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mission_id", name="uq_mission_resource_policies_mission"),
    )
    _indexes(
        "mission_resource_policies",
        ("workspace_id", "mission_id", "allocation_mode", "created_at"),
    )

    op.create_table(
        "mission_resource_allocations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("requested_by", sa.String(length=255), nullable=False),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
        sa.Column("budget_usd", sa.Float(), nullable=False),
        sa.Column("actual_cost_usd", sa.Float(), nullable=False),
        sa.Column("agent_slots", sa.Integer(), nullable=False),
        sa.Column("tool_slots", sa.Integer(), nullable=False),
        sa.Column("compute_units", sa.Float(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("admission_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending_approval', 'approved', 'reserved', 'active', 'released', 'consumed', 'exceeded', 'cancelled')",
            name="ck_mission_resource_allocations_status",
        ),
        sa.CheckConstraint(
            "budget_usd >= 0",
            name="ck_mission_resource_allocations_budget",
        ),
        sa.CheckConstraint(
            "actual_cost_usd >= 0",
            name="ck_mission_resource_allocations_actual_cost",
        ),
        sa.CheckConstraint(
            "agent_slots >= 0",
            name="ck_mission_resource_allocations_agent_slots",
        ),
        sa.CheckConstraint(
            "tool_slots >= 0",
            name="ck_mission_resource_allocations_tool_slots",
        ),
        sa.CheckConstraint(
            "compute_units >= 0",
            name="ck_mission_resource_allocations_compute_units",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_mission_resource_allocations_version",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cycle_id"], ["mission_cycles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_id"], ["mission_strategies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cycle_id", name="uq_mission_resource_allocations_cycle"),
    )
    _indexes(
        "mission_resource_allocations",
        (
            "workspace_id",
            "mission_id",
            "cycle_id",
            "strategy_id",
            "status",
            "approved_by",
            "created_at",
        ),
    )

    op.create_table(
        "mission_resource_usage",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=True),
        sa.Column("allocation_id", sa.String(length=64), nullable=True),
        sa.Column("strategy_id", sa.String(length=64), nullable=True),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=64), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_ref", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "category IN ('llm', 'tool', 'compute', 'storage', 'network', 'human', 'other')",
            name="ck_mission_resource_usage_category",
        ),
        sa.CheckConstraint(
            "quantity >= 0",
            name="ck_mission_resource_usage_quantity",
        ),
        sa.CheckConstraint(
            "cost_usd >= 0",
            name="ck_mission_resource_usage_cost",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cycle_id"], ["mission_cycles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["allocation_id"], ["mission_resource_allocations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["strategy_id"], ["mission_strategies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mission_id",
            "idempotency_key",
            name="uq_mission_resource_usage_idempotency",
        ),
    )
    _indexes(
        "mission_resource_usage",
        (
            "workspace_id",
            "mission_id",
            "cycle_id",
            "allocation_id",
            "strategy_id",
            "category",
            "source_ref",
            "occurred_at",
            "created_at",
        ),
    )

    op.create_table(
        "mission_capacity_plans",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("sample_cycles", sa.Integer(), nullable=False),
        sa.Column("confidence_percent", sa.Float(), nullable=False),
        sa.Column("recommended_cycle_budget_usd", sa.Float(), nullable=False),
        sa.Column("recommended_parallel_cycles", sa.Integer(), nullable=False),
        sa.Column("recommended_agent_slots", sa.Integer(), nullable=False),
        sa.Column("recommended_tool_slots", sa.Integer(), nullable=False),
        sa.Column("recommended_compute_units", sa.Float(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("calculation_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'recommended', 'applied', 'rejected')",
            name="ck_mission_capacity_plans_status",
        ),
        sa.CheckConstraint(
            "sample_cycles >= 0",
            name="ck_mission_capacity_plans_sample_cycles",
        ),
        sa.CheckConstraint(
            "confidence_percent >= 0 AND confidence_percent <= 100",
            name="ck_mission_capacity_plans_confidence",
        ),
        sa.CheckConstraint(
            "recommended_cycle_budget_usd >= 0",
            name="ck_mission_capacity_plans_cycle_budget",
        ),
        sa.CheckConstraint(
            "recommended_parallel_cycles >= 1",
            name="ck_mission_capacity_plans_parallel_cycles",
        ),
        sa.CheckConstraint(
            "recommended_agent_slots >= 1",
            name="ck_mission_capacity_plans_agent_slots",
        ),
        sa.CheckConstraint(
            "recommended_tool_slots >= 1",
            name="ck_mission_capacity_plans_tool_slots",
        ),
        sa.CheckConstraint(
            "recommended_compute_units >= 0",
            name="ck_mission_capacity_plans_compute_units",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes(
        "mission_capacity_plans",
        ("workspace_id", "mission_id", "status", "created_at"),
    )


def downgrade() -> None:
    _drop_indexes(
        "mission_capacity_plans",
        ("workspace_id", "mission_id", "status", "created_at"),
    )
    op.drop_table("mission_capacity_plans")

    _drop_indexes(
        "mission_resource_usage",
        (
            "workspace_id",
            "mission_id",
            "cycle_id",
            "allocation_id",
            "strategy_id",
            "category",
            "source_ref",
            "occurred_at",
            "created_at",
        ),
    )
    op.drop_table("mission_resource_usage")

    _drop_indexes(
        "mission_resource_allocations",
        (
            "workspace_id",
            "mission_id",
            "cycle_id",
            "strategy_id",
            "status",
            "approved_by",
            "created_at",
        ),
    )
    op.drop_table("mission_resource_allocations")

    _drop_indexes(
        "mission_resource_policies",
        ("workspace_id", "mission_id", "allocation_mode", "created_at"),
    )
    op.drop_table("mission_resource_policies")
