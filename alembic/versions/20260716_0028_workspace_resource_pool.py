"""Workspace shared budget, capacity pool and resource conflict resolution.

Revision ID: 20260716_0028
Revises: 20260716_0027
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Iterable

from alembic import op
import sqlalchemy as sa


revision = "20260716_0028"
down_revision = "20260716_0027"
branch_labels = None
depends_on = None


def _indexes(table: str, columns: Iterable[str]) -> None:
    for column in columns:
        op.create_index(
            f"ix_{table}_{column}",
            table,
            [column],
            unique=False,
        )


def _drop_indexes(table: str, columns: Iterable[str]) -> None:
    for column in reversed(tuple(columns)):
        op.drop_index(f"ix_{table}_{column}", table_name=table)


def upgrade() -> None:
    op.create_table(
        "workspace_resource_policies",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("allocation_mode", sa.String(length=32), nullable=False),
        sa.Column("require_human_approval", sa.Boolean(), nullable=False),
        sa.Column("auto_rebalance_enabled", sa.Boolean(), nullable=False),
        sa.Column("enforce_cycle_admission", sa.Boolean(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("total_budget_usd", sa.Float(), nullable=False),
        sa.Column("default_cycle_budget_usd", sa.Float(), nullable=False),
        sa.Column("max_cycle_budget_usd", sa.Float(), nullable=False),
        sa.Column("reserve_percent", sa.Float(), nullable=False),
        sa.Column("max_parallel_cycles", sa.Integer(), nullable=False),
        sa.Column("agent_slots", sa.Integer(), nullable=False),
        sa.Column("tool_slots", sa.Integer(), nullable=False),
        sa.Column("compute_units", sa.Float(), nullable=False),
        sa.Column("min_mission_guarantee_percent", sa.Float(), nullable=False),
        sa.Column("overcommit_tolerance_percent", sa.Float(), nullable=False),
        sa.Column("rebalance_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("last_rebalanced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "allocation_mode IN ('manual', 'weighted', 'adaptive')",
            name="ck_workspace_resource_policies_mode",
        ),
        sa.CheckConstraint(
            "total_budget_usd >= 0",
            name="ck_workspace_resource_policies_total_budget",
        ),
        sa.CheckConstraint(
            "default_cycle_budget_usd >= 0",
            name="ck_workspace_resource_policies_default_cycle_budget",
        ),
        sa.CheckConstraint(
            "max_cycle_budget_usd >= 0",
            name="ck_workspace_resource_policies_max_cycle_budget",
        ),
        sa.CheckConstraint(
            "reserve_percent >= 0 AND reserve_percent <= 100",
            name="ck_workspace_resource_policies_reserve_percent",
        ),
        sa.CheckConstraint(
            "max_parallel_cycles >= 1",
            name="ck_workspace_resource_policies_parallel_cycles",
        ),
        sa.CheckConstraint(
            "agent_slots >= 1",
            name="ck_workspace_resource_policies_agent_slots",
        ),
        sa.CheckConstraint(
            "tool_slots >= 1",
            name="ck_workspace_resource_policies_tool_slots",
        ),
        sa.CheckConstraint(
            "compute_units >= 0",
            name="ck_workspace_resource_policies_compute_units",
        ),
        sa.CheckConstraint(
            "min_mission_guarantee_percent >= 0 AND "
            "min_mission_guarantee_percent <= 100",
            name="ck_workspace_resource_policies_min_guarantee",
        ),
        sa.CheckConstraint(
            "overcommit_tolerance_percent >= 0 AND "
            "overcommit_tolerance_percent <= 100",
            name="ck_workspace_resource_policies_overcommit",
        ),
        sa.CheckConstraint(
            "rebalance_interval_seconds >= 30",
            name="ck_workspace_resource_policies_rebalance_interval",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            name="uq_workspace_resource_policies_workspace",
        ),
    )
    _indexes(
        "workspace_resource_policies",
        (
            "workspace_id",
            "allocation_mode",
            "last_rebalanced_at",
            "created_at",
        ),
    )

    op.create_table(
        "workspace_resource_reservations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=False),
        sa.Column("allocation_id", sa.String(length=64), nullable=True),
        sa.Column("policy_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
        sa.Column("budget_usd", sa.Float(), nullable=False),
        sa.Column("actual_cost_usd", sa.Float(), nullable=False),
        sa.Column("agent_slots", sa.Integer(), nullable=False),
        sa.Column("tool_slots", sa.Integer(), nullable=False),
        sa.Column("compute_units", sa.Float(), nullable=False),
        sa.Column("priority_score", sa.Float(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("forced", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
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
            "status IN ('pending_approval', 'reserved', 'active', 'released', "
            "'consumed', 'exceeded', 'cancelled')",
            name="ck_workspace_resource_reservations_status",
        ),
        sa.CheckConstraint(
            "budget_usd >= 0",
            name="ck_workspace_resource_reservations_budget",
        ),
        sa.CheckConstraint(
            "actual_cost_usd >= 0",
            name="ck_workspace_resource_reservations_actual_cost",
        ),
        sa.CheckConstraint(
            "agent_slots >= 0",
            name="ck_workspace_resource_reservations_agent_slots",
        ),
        sa.CheckConstraint(
            "tool_slots >= 0",
            name="ck_workspace_resource_reservations_tool_slots",
        ),
        sa.CheckConstraint(
            "compute_units >= 0",
            name="ck_workspace_resource_reservations_compute_units",
        ),
        sa.CheckConstraint(
            "priority_score >= 0 AND priority_score <= 100",
            name="ck_workspace_resource_reservations_priority_score",
        ),
        sa.CheckConstraint(
            "rank >= 0",
            name="ck_workspace_resource_reservations_rank",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_workspace_resource_reservations_version",
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
            ["allocation_id"],
            ["mission_resource_allocations.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["workspace_resource_policies.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "cycle_id", name="uq_workspace_resource_reservations_cycle"
        ),
        sa.UniqueConstraint(
            "allocation_id",
            name="uq_workspace_resource_reservations_allocation",
        ),
    )
    _indexes(
        "workspace_resource_reservations",
        (
            "workspace_id",
            "mission_id",
            "cycle_id",
            "allocation_id",
            "policy_id",
            "status",
            "actor_id",
            "approved_by",
            "priority_score",
            "rank",
            "created_at",
        ),
    )

    op.create_table(
        "workspace_resource_conflicts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=True),
        sa.Column("reservation_id", sa.String(length=64), nullable=True),
        sa.Column("allocation_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("resource_types_json", sa.JSON(), nullable=False),
        sa.Column("requested_json", sa.JSON(), nullable=False),
        sa.Column("available_json", sa.JSON(), nullable=False),
        sa.Column("shortfall_json", sa.JSON(), nullable=False),
        sa.Column("conflicting_reservation_ids_json", sa.JSON(), nullable=False),
        sa.Column("recommended_action", sa.String(length=32), nullable=False),
        sa.Column("resolution_action", sa.String(length=32), nullable=True),
        sa.Column("resolution_reason", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('open', 'resolved', 'waived', 'cancelled')",
            name="ck_workspace_resource_conflicts_status",
        ),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_workspace_resource_conflicts_severity",
        ),
        sa.CheckConstraint(
            "recommended_action IN ('defer', 'preempt', 'rebalance', "
            "'increase_capacity', 'force', 'manual_review')",
            name="ck_workspace_resource_conflicts_recommended_action",
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
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["workspace_resource_reservations.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["allocation_id"],
            ["mission_resource_allocations.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dedupe_key", name="uq_workspace_resource_conflicts_dedupe"
        ),
    )
    _indexes(
        "workspace_resource_conflicts",
        (
            "workspace_id",
            "mission_id",
            "cycle_id",
            "reservation_id",
            "allocation_id",
            "status",
            "severity",
            "recommended_action",
            "resolution_action",
            "resolved_by",
            "created_at",
            "resolved_at",
        ),
    )

    op.create_table(
        "workspace_resource_rebalances",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("proposal_json", sa.JSON(), nullable=False),
        sa.Column("applied_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'recommended', 'applied', 'rejected')",
            name="ck_workspace_resource_rebalances_status",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["workspace_resource_policies.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes(
        "workspace_resource_rebalances",
        (
            "workspace_id",
            "policy_id",
            "status",
            "actor_id",
            "created_at",
            "applied_at",
        ),
    )


def downgrade() -> None:
    _drop_indexes(
        "workspace_resource_rebalances",
        (
            "workspace_id",
            "policy_id",
            "status",
            "actor_id",
            "created_at",
            "applied_at",
        ),
    )
    op.drop_table("workspace_resource_rebalances")

    _drop_indexes(
        "workspace_resource_conflicts",
        (
            "workspace_id",
            "mission_id",
            "cycle_id",
            "reservation_id",
            "allocation_id",
            "status",
            "severity",
            "recommended_action",
            "resolution_action",
            "resolved_by",
            "created_at",
            "resolved_at",
        ),
    )
    op.drop_table("workspace_resource_conflicts")

    _drop_indexes(
        "workspace_resource_reservations",
        (
            "workspace_id",
            "mission_id",
            "cycle_id",
            "allocation_id",
            "policy_id",
            "status",
            "actor_id",
            "approved_by",
            "priority_score",
            "rank",
            "created_at",
        ),
    )
    op.drop_table("workspace_resource_reservations")

    _drop_indexes(
        "workspace_resource_policies",
        (
            "workspace_id",
            "allocation_mode",
            "last_rebalanced_at",
            "created_at",
        ),
    )
    op.drop_table("workspace_resource_policies")
