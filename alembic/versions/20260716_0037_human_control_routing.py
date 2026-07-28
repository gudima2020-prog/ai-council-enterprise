"""P1-019.7 on-call routing, availability and notification escalation.

Revision ID: 20260716_0037
Revises: 20260716_0036
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260716_0037"
down_revision = "20260716_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "human_control_on_call_schedules",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scope_key", sa.String(320), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("schedule_key", sa.String(160), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("timezone", sa.String(96), nullable=False, server_default="UTC"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("routing_strategy", sa.String(32), nullable=False, server_default="first_available"),
        sa.Column("fallback_role_keys_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("scope_key", name="uq_human_control_on_call_schedules_scope"),
        sa.CheckConstraint(
            "routing_strategy IN ('first_available', 'round_robin', 'broadcast', 'primary_backup')",
            name="ck_human_control_on_call_schedules_strategy",
        ),
    )
    for name, columns in (
        ("ix_human_control_on_call_schedules_workspace_id", ["workspace_id"]),
        ("ix_human_control_on_call_schedules_schedule_key", ["schedule_key"]),
        ("ix_human_control_on_call_schedules_created_at", ["created_at"]),
        ("ix_human_control_on_call_schedules_lookup", ["workspace_id", "enabled", "schedule_key"]),
    ):
        op.create_index(name, "human_control_on_call_schedules", columns, unique=False)

    op.create_table(
        "human_control_on_call_members",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("schedule_id", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("role_key", sa.String(96), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("is_backup", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("weekdays_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("start_time", sa.String(5), nullable=True),
        sa.Column("end_time", sa.String(5), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_active_notifications", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["schedule_id"], ["human_control_on_call_schedules.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("schedule_id", "actor_id", name="uq_human_control_on_call_members_actor"),
        sa.CheckConstraint("priority >= 0 AND priority <= 100", name="ck_human_control_on_call_members_priority"),
        sa.CheckConstraint("max_active_notifications >= 0", name="ck_human_control_on_call_members_load"),
    )
    for name, columns in (
        ("ix_human_control_on_call_members_schedule_id", ["schedule_id"]),
        ("ix_human_control_on_call_members_actor_id", ["actor_id"]),
        ("ix_human_control_on_call_members_role_key", ["role_key"]),
        ("ix_human_control_on_call_members_valid_from", ["valid_from"]),
        ("ix_human_control_on_call_members_valid_until", ["valid_until"]),
        ("ix_human_control_on_call_members_created_at", ["created_at"]),
        ("ix_human_control_on_call_members_active", ["schedule_id", "enabled", "priority", "is_backup"]),
    ):
        op.create_index(name, "human_control_on_call_members", columns, unique=False)

    op.create_table(
        "human_control_operator_availability",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_actor_key", sa.String(384), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("capacity_percent", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("active_notification_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("available_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("workspace_actor_key", name="uq_human_control_operator_availability_actor"),
        sa.CheckConstraint(
            "status IN ('available', 'busy', 'offline', 'do_not_disturb', 'unknown')",
            name="ck_human_control_operator_availability_status",
        ),
        sa.CheckConstraint(
            "source IN ('manual', 'heartbeat', 'schedule', 'system')",
            name="ck_human_control_operator_availability_source",
        ),
        sa.CheckConstraint(
            "capacity_percent >= 0 AND capacity_percent <= 100",
            name="ck_human_control_operator_availability_capacity",
        ),
    )
    for name, columns in (
        ("ix_human_control_operator_availability_workspace_id", ["workspace_id"]),
        ("ix_human_control_operator_availability_actor_id", ["actor_id"]),
        ("ix_human_control_operator_availability_status", ["status"]),
        ("ix_human_control_operator_availability_source", ["source"]),
        ("ix_human_control_operator_availability_available_until", ["available_until"]),
        ("ix_human_control_operator_availability_last_seen_at", ["last_seen_at"]),
        ("ix_human_control_operator_availability_created_at", ["created_at"]),
        ("ix_human_control_operator_availability_lookup", ["workspace_id", "status", "available_until", "last_seen_at"]),
    ):
        op.create_index(name, "human_control_operator_availability", columns, unique=False)

    op.create_table(
        "human_control_notification_routing_rules",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scope_key", sa.String(320), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("rule_key", sa.String(160), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("rule_priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("event_patterns_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("source_types_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("risk_levels_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("min_priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("schedule_id", sa.String(64), nullable=True),
        sa.Column("role_keys_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("fallback_actor_ids_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("strategy", sa.String(32), nullable=False, server_default="first_available"),
        sa.Column("availability_required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("min_capacity_percent", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("heartbeat_ttl_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("max_recipients", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("fallback_mode", sa.String(32), nullable=False, server_default="base_recipients"),
        sa.Column("ack_required", sa.Boolean(), nullable=True),
        sa.Column("ack_timeout_seconds", sa.Integer(), nullable=True),
        sa.Column("last_selected_actor_id", sa.String(255), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["schedule_id"], ["human_control_on_call_schedules.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("scope_key", name="uq_human_control_notification_routing_rules_scope"),
        sa.CheckConstraint(
            "strategy IN ('first_available', 'round_robin', 'broadcast', 'primary_backup')",
            name="ck_human_control_notification_routing_rules_strategy",
        ),
        sa.CheckConstraint(
            "fallback_mode IN ('base_recipients', 'fallback_actors', 'broadcast_roles', 'fail_closed')",
            name="ck_human_control_notification_routing_rules_fallback",
        ),
        sa.CheckConstraint("min_priority >= 0 AND min_priority <= 100", name="ck_human_control_notification_routing_rules_priority"),
        sa.CheckConstraint("min_capacity_percent >= 0 AND min_capacity_percent <= 100", name="ck_human_control_notification_routing_rules_capacity"),
        sa.CheckConstraint("max_recipients >= 1 AND max_recipients <= 100", name="ck_human_control_notification_routing_rules_recipients"),
    )
    for name, columns in (
        ("ix_human_control_notification_routing_rules_workspace_id", ["workspace_id"]),
        ("ix_human_control_notification_routing_rules_rule_key", ["rule_key"]),
        ("ix_human_control_notification_routing_rules_schedule_id", ["schedule_id"]),
        ("ix_human_control_notification_routing_rules_created_at", ["created_at"]),
        ("ix_human_control_notification_routing_rules_match", ["workspace_id", "enabled", "rule_priority"]),
    ):
        op.create_index(name, "human_control_notification_routing_rules", columns, unique=False)

    op.create_table(
        "human_control_notification_escalation_rules",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scope_key", sa.String(320), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("rule_key", sa.String(160), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("rule_priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("event_patterns_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("source_types_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("risk_levels_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("min_priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trigger_on_json", sa.JSON(), nullable=False, server_default='["ack_overdue"]'),
        sa.Column("initial_delay_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("repeat_interval_seconds", sa.Integer(), nullable=False, server_default="900"),
        sa.Column("max_escalations", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("target_schedule_id", sa.String(64), nullable=True),
        sa.Column("target_role_keys_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("target_actor_ids_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("channel_id", sa.String(64), nullable=True),
        sa.Column("strategy", sa.String(32), nullable=False, server_default="first_available"),
        sa.Column("priority_increment", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("ack_required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("ack_timeout_seconds", sa.Integer(), nullable=False, server_default="900"),
        sa.Column("auto_resolve_on_ack", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_schedule_id"], ["human_control_on_call_schedules.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["channel_id"], ["human_control_notification_channels.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("scope_key", name="uq_human_control_notification_escalation_rules_scope"),
        sa.CheckConstraint("min_priority >= 0 AND min_priority <= 100", name="ck_human_control_notification_escalation_rules_priority"),
        sa.CheckConstraint("max_escalations >= 1 AND max_escalations <= 20", name="ck_human_control_notification_escalation_rules_count"),
        sa.CheckConstraint("priority_increment >= 0 AND priority_increment <= 100", name="ck_human_control_notification_escalation_rules_increment"),
    )
    for name, columns in (
        ("ix_human_control_notification_escalation_rules_workspace_id", ["workspace_id"]),
        ("ix_human_control_notification_escalation_rules_rule_key", ["rule_key"]),
        ("ix_human_control_notification_escalation_rules_target_schedule_id", ["target_schedule_id"]),
        ("ix_human_control_notification_escalation_rules_channel_id", ["channel_id"]),
        ("ix_human_control_notification_escalation_rules_created_at", ["created_at"]),
        ("ix_human_control_notification_escalation_rules_match", ["workspace_id", "enabled", "rule_priority"]),
    ):
        op.create_index(name, "human_control_notification_escalation_rules", columns, unique=False)

    op.create_table(
        "human_control_notification_escalations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("original_notification_id", sa.String(64), nullable=True),
        sa.Column("rule_id", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("trigger_type", sa.String(32), nullable=False),
        sa.Column("escalation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_escalation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("spawned_notification_ids_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("resolved_by", sa.String(255), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["original_notification_id"], ["human_control_notifications.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["rule_id"], ["human_control_notification_escalation_rules.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("idempotency_key", name="uq_human_control_notification_escalations_idempotency"),
        sa.CheckConstraint("status IN ('open', 'resolved', 'exhausted', 'cancelled')", name="ck_human_control_notification_escalations_status"),
        sa.CheckConstraint("trigger_type IN ('ack_overdue', 'delivery_failed', 'unroutable', 'manual')", name="ck_human_control_notification_escalations_trigger"),
    )
    for name, columns in (
        ("ix_human_control_notification_escalations_workspace_id", ["workspace_id"]),
        ("ix_human_control_notification_escalations_original_notification_id", ["original_notification_id"]),
        ("ix_human_control_notification_escalations_rule_id", ["rule_id"]),
        ("ix_human_control_notification_escalations_idempotency_key", ["idempotency_key"]),
        ("ix_human_control_notification_escalations_status", ["status"]),
        ("ix_human_control_notification_escalations_trigger_type", ["trigger_type"]),
        ("ix_human_control_notification_escalations_next_escalation_at", ["next_escalation_at"]),
        ("ix_human_control_notification_escalations_created_at", ["created_at"]),
        ("ix_human_control_notification_escalations_queue", ["status", "next_escalation_at", "created_at"]),
    ):
        op.create_index(name, "human_control_notification_escalations", columns, unique=False)

    op.create_table(
        "human_control_notification_escalation_attempts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("escalation_id", sa.String(64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("target_actor_ids_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("notification_ids_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["escalation_id"], ["human_control_notification_escalations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("escalation_id", "sequence", name="uq_human_control_notification_escalation_attempts_sequence"),
        sa.CheckConstraint("status IN ('started', 'routed', 'no_candidate', 'failed')", name="ck_human_control_notification_escalation_attempts_status"),
    )
    for name, columns in (
        ("ix_human_control_notification_escalation_attempts_escalation_id", ["escalation_id"]),
        ("ix_human_control_notification_escalation_attempts_status", ["status"]),
        ("ix_human_control_notification_escalation_attempts_created_at", ["created_at"]),
        ("ix_human_control_notification_escalation_attempts_history", ["escalation_id", "sequence", "created_at"]),
    ):
        op.create_index(name, "human_control_notification_escalation_attempts", columns, unique=False)


def downgrade() -> None:
    op.drop_table("human_control_notification_escalation_attempts")
    op.drop_table("human_control_notification_escalations")
    op.drop_table("human_control_notification_escalation_rules")
    op.drop_table("human_control_notification_routing_rules")
    op.drop_table("human_control_operator_availability")
    op.drop_table("human_control_on_call_members")
    op.drop_table("human_control_on_call_schedules")
