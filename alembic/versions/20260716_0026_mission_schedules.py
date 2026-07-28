"""Mission schedule portfolio, deadlines and adaptive cadence.

Revision ID: 20260716_0026
Revises: 20260716_0025
Create Date: 2026-07-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260716_0026"
down_revision: Union[str, Sequence[str], None] = "20260716_0025"
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
        "mission_schedule_policies",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("schedule_mode", sa.String(length=32), nullable=False),
        sa.Column("timezone_name", sa.String(length=128), nullable=False),
        sa.Column("base_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("min_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("max_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("adaptive_enabled", sa.Boolean(), nullable=False),
        sa.Column("require_human_approval", sa.Boolean(), nullable=False),
        sa.Column("auto_apply_enabled", sa.Boolean(), nullable=False),
        sa.Column("enforce_manual_cycles", sa.Boolean(), nullable=False),
        sa.Column("allowed_weekdays_json", sa.JSON(), nullable=False),
        sa.Column("quiet_hours_start", sa.String(length=5), nullable=True),
        sa.Column("quiet_hours_end", sa.String(length=5), nullable=True),
        sa.Column("deadline_warning_seconds", sa.Integer(), nullable=False),
        sa.Column("overdue_action", sa.String(length=32), nullable=False),
        sa.Column("target_cycles_per_day", sa.Float(), nullable=False),
        sa.Column("adaptation_sample_cycles", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("schedule_mode IN ('manual', 'fixed', 'adaptive')", name="ck_mission_schedule_policies_mode"),
        sa.CheckConstraint("overdue_action IN ('observe', 'pause', 'escalate')", name="ck_mission_schedule_policies_overdue_action"),
        sa.CheckConstraint("base_interval_seconds >= 30", name="ck_mission_schedule_policies_base_interval"),
        sa.CheckConstraint("min_interval_seconds >= 30", name="ck_mission_schedule_policies_min_interval"),
        sa.CheckConstraint("max_interval_seconds >= min_interval_seconds", name="ck_mission_schedule_policies_max_interval"),
        sa.CheckConstraint("base_interval_seconds >= min_interval_seconds AND base_interval_seconds <= max_interval_seconds", name="ck_mission_schedule_policies_interval_range"),
        sa.CheckConstraint("target_cycles_per_day >= 0 AND target_cycles_per_day <= 96", name="ck_mission_schedule_policies_target_cycles"),
        sa.CheckConstraint("adaptation_sample_cycles >= 1", name="ck_mission_schedule_policies_sample_cycles"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mission_id", name="uq_mission_schedule_policies_mission"),
    )
    _indexes("mission_schedule_policies", ("workspace_id", "mission_id", "schedule_mode", "overdue_action", "created_at"))

    op.create_table(
        "mission_schedule_windows",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("weekdays_json", sa.JSON(), nullable=False),
        sa.Column("start_time", sa.String(length=5), nullable=False),
        sa.Column("end_time", sa.String(length=5), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("max_cycles", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("priority >= 0 AND priority <= 100", name="ck_mission_schedule_windows_priority"),
        sa.CheckConstraint("max_cycles >= 0", name="ck_mission_schedule_windows_max_cycles"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes("mission_schedule_windows", ("workspace_id", "mission_id", "starts_at", "ends_at", "priority", "created_at"))

    op.create_table(
        "mission_schedule_evaluations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=True),
        sa.Column("policy_id", sa.String(length=64), nullable=True),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("previous_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("recommended_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("applied_interval_seconds", sa.Integer(), nullable=True),
        sa.Column("urgency_score", sa.Float(), nullable=False),
        sa.Column("failure_rate_percent", sa.Float(), nullable=False),
        sa.Column("progress_velocity_percent", sa.Float(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("decision IN ('keep', 'accelerate', 'slow_down', 'pause')", name="ck_mission_schedule_evaluations_decision"),
        sa.CheckConstraint("urgency_score >= 0 AND urgency_score <= 100", name="ck_mission_schedule_evaluations_urgency"),
        sa.CheckConstraint("failure_rate_percent >= 0 AND failure_rate_percent <= 100", name="ck_mission_schedule_evaluations_failure_rate"),
        sa.CheckConstraint("progress_velocity_percent >= 0 AND progress_velocity_percent <= 100", name="ck_mission_schedule_evaluations_progress_velocity"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cycle_id"], ["mission_cycles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["policy_id"], ["mission_schedule_policies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes("mission_schedule_evaluations", ("workspace_id", "mission_id", "cycle_id", "policy_id", "actor_id", "decision", "created_at"))

    op.create_table(
        "mission_deadline_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=255), nullable=True),
        sa.Column("resolution_reason", sa.Text(), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.CheckConstraint("event_type IN ('approaching', 'overdue', 'recovered')", name="ck_mission_deadline_events_type"),
        sa.CheckConstraint("severity IN ('info', 'warning', 'critical')", name="ck_mission_deadline_events_severity"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mission_id"], ["workspace_missions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes("mission_deadline_events", ("workspace_id", "mission_id", "event_type", "severity", "deadline_at", "detected_at", "resolved_at", "resolved_by"))


def downgrade() -> None:
    _drop_indexes("mission_deadline_events", ("workspace_id", "mission_id", "event_type", "severity", "deadline_at", "detected_at", "resolved_at", "resolved_by"))
    op.drop_table("mission_deadline_events")
    _drop_indexes("mission_schedule_evaluations", ("workspace_id", "mission_id", "cycle_id", "policy_id", "actor_id", "decision", "created_at"))
    op.drop_table("mission_schedule_evaluations")
    _drop_indexes("mission_schedule_windows", ("workspace_id", "mission_id", "starts_at", "ends_at", "priority", "created_at"))
    op.drop_table("mission_schedule_windows")
    _drop_indexes("mission_schedule_policies", ("workspace_id", "mission_id", "schedule_mode", "overdue_action", "created_at"))
    op.drop_table("mission_schedule_policies")
