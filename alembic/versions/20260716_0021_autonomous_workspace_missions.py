"""Autonomous Workspace policies and long-running missions

Revision ID: 20260716_0021
Revises: 20260715_0020
Create Date: 2026-07-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260716_0021"
down_revision: Union[str, Sequence[str], None] = "20260715_0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "autonomous_workspace_policies",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("autonomy_mode", sa.String(length=32), nullable=False),
        sa.Column("max_active_missions", sa.Integer(), nullable=False),
        sa.Column("max_parallel_cycles", sa.Integer(), nullable=False),
        sa.Column("cycle_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("require_user_approval", sa.Boolean(), nullable=False),
        sa.Column("allow_auto_start", sa.Boolean(), nullable=False),
        sa.Column("planner_ref", sa.String(length=255), nullable=False),
        sa.Column("planning_mode", sa.String(length=32), nullable=False),
        sa.Column("auto_validate", sa.Boolean(), nullable=False),
        sa.Column("auto_assign", sa.Boolean(), nullable=False),
        sa.Column("strict_assignment", sa.Boolean(), nullable=False),
        sa.Column("auto_review", sa.Boolean(), nullable=False),
        sa.Column("review_threshold", sa.Integer(), nullable=False),
        sa.Column("auto_fix_review", sa.Boolean(), nullable=False),
        sa.Column("max_review_rounds", sa.Integer(), nullable=False),
        sa.Column("require_review_pass", sa.Boolean(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "autonomy_mode IN ('observe', 'supervised', 'autonomous')",
            name="ck_autonomous_workspace_policies_mode",
        ),
        sa.CheckConstraint(
            "max_active_missions >= 1",
            name="ck_autonomous_workspace_policies_max_missions",
        ),
        sa.CheckConstraint(
            "max_parallel_cycles >= 1",
            name="ck_autonomous_workspace_policies_max_cycles",
        ),
        sa.CheckConstraint(
            "cycle_interval_seconds >= 30",
            name="ck_autonomous_workspace_policies_interval",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            name="uq_autonomous_workspace_policies_workspace",
        ),
    )
    for name in ("workspace_id", "autonomy_mode", "created_at"):
        op.create_index(
            f"ix_autonomous_workspace_policies_{name}",
            "autonomous_workspace_policies",
            [name],
        )

    op.create_table(
        "workspace_missions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("success_criteria", sa.Text(), nullable=False),
        sa.Column("strategy_hint", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("planner_ref", sa.String(length=255), nullable=True),
        sa.Column("planning_mode", sa.String(length=32), nullable=True),
        sa.Column("auto_start_plans", sa.Boolean(), nullable=True),
        sa.Column("max_cycles", sa.Integer(), nullable=False),
        sa.Column("cycle_count", sa.Integer(), nullable=False),
        sa.Column("progress_percent", sa.Float(), nullable=False),
        sa.Column("current_cycle_id", sa.String(length=64), nullable=True),
        sa.Column("next_cycle_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pause_reason", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'paused', 'completed', 'failed', 'cancelled')",
            name="ck_workspace_missions_status",
        ),
        sa.CheckConstraint(
            "priority >= 0 AND priority <= 100",
            name="ck_workspace_missions_priority",
        ),
        sa.CheckConstraint(
            "progress_percent >= 0 AND progress_percent <= 100",
            name="ck_workspace_missions_progress",
        ),
        sa.CheckConstraint(
            "max_cycles >= 1",
            name="ck_workspace_missions_max_cycles",
        ),
        sa.CheckConstraint(
            "cycle_count >= 0",
            name="ck_workspace_missions_cycle_count",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for name in (
        "workspace_id",
        "status",
        "priority",
        "current_cycle_id",
        "next_cycle_at",
        "deadline_at",
        "last_activity_at",
        "created_at",
    ):
        op.create_index(
            f"ix_workspace_missions_{name}",
            "workspace_missions",
            [name],
        )

    op.create_table(
        "mission_goals",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("goal_key", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("success_criteria", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress_percent", sa.Float(), nullable=False),
        sa.Column("depends_on_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'achieved', 'failed', 'cancelled')",
            name="ck_mission_goals_status",
        ),
        sa.CheckConstraint("weight > 0", name="ck_mission_goals_weight"),
        sa.CheckConstraint(
            "progress_percent >= 0 AND progress_percent <= 100",
            name="ck_mission_goals_progress",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["workspace_missions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mission_id",
            "goal_key",
            name="uq_mission_goals_key",
        ),
    )
    for name in ("mission_id", "status"):
        op.create_index(
            f"ix_mission_goals_{name}",
            "mission_goals",
            [name],
        )

    op.create_table(
        "mission_cycles",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("goal_ids_json", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("planner_run_id", sa.String(length=64), nullable=True),
        sa.Column("execution_plan_id", sa.String(length=64), nullable=True),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'planning', 'ready', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_mission_cycles_status",
        ),
        sa.CheckConstraint(
            "trigger IN ('manual', 'scheduled', 'event', 'recovery')",
            name="ck_mission_cycles_trigger",
        ),
        sa.CheckConstraint(
            "cycle_number >= 1",
            name="ck_mission_cycles_number",
        ),
        sa.ForeignKeyConstraint(
            ["execution_plan_id"],
            ["execution_plans.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["workspace_missions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mission_id",
            "cycle_number",
            name="uq_mission_cycles_number",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_mission_cycles_idempotency",
        ),
    )
    for name in (
        "mission_id",
        "status",
        "trigger",
        "idempotency_key",
        "planner_run_id",
        "execution_plan_id",
        "scheduled_for",
        "created_at",
    ):
        op.create_index(
            f"ix_mission_cycles_{name}",
            "mission_cycles",
            [name],
        )

    op.create_table(
        "mission_progress_updates",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("goal_id", sa.String(length=64), nullable=True),
        sa.Column("cycle_id", sa.String(length=64), nullable=True),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("previous_progress_percent", sa.Float(), nullable=False),
        sa.Column("new_progress_percent", sa.Float(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["cycle_id"],
            ["mission_cycles.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["goal_id"],
            ["mission_goals.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["workspace_missions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for name in ("mission_id", "goal_id", "cycle_id", "actor_id", "created_at"):
        op.create_index(
            f"ix_mission_progress_updates_{name}",
            "mission_progress_updates",
            [name],
        )


def downgrade() -> None:
    for name in reversed(("mission_id", "goal_id", "cycle_id", "actor_id", "created_at")):
        op.drop_index(
            f"ix_mission_progress_updates_{name}",
            table_name="mission_progress_updates",
        )
    op.drop_table("mission_progress_updates")

    for name in reversed((
        "mission_id",
        "status",
        "trigger",
        "idempotency_key",
        "planner_run_id",
        "execution_plan_id",
        "scheduled_for",
        "created_at",
    )):
        op.drop_index(
            f"ix_mission_cycles_{name}",
            table_name="mission_cycles",
        )
    op.drop_table("mission_cycles")

    for name in reversed(("mission_id", "status")):
        op.drop_index(
            f"ix_mission_goals_{name}",
            table_name="mission_goals",
        )
    op.drop_table("mission_goals")

    for name in reversed((
        "workspace_id",
        "status",
        "priority",
        "current_cycle_id",
        "next_cycle_at",
        "deadline_at",
        "last_activity_at",
        "created_at",
    )):
        op.drop_index(
            f"ix_workspace_missions_{name}",
            table_name="workspace_missions",
        )
    op.drop_table("workspace_missions")

    for name in reversed(("workspace_id", "autonomy_mode", "created_at")):
        op.drop_index(
            f"ix_autonomous_workspace_policies_{name}",
            table_name="autonomous_workspace_policies",
        )
    op.drop_table("autonomous_workspace_policies")
