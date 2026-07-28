"""P1-020.3 external providers, rotation lifecycle and health monitoring.

Revision ID: 20260716_0043
Revises: 20260716_0042
"""
from alembic import op
import sqlalchemy as sa

revision = "20260716_0043"
down_revision = "20260716_0042"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "secret_rotation_policies",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("secret_id", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("rotation_mode", sa.String(32), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("warning_seconds", sa.Integer(), nullable=False),
        sa.Column("auto_rotate_enabled", sa.Boolean(), nullable=False),
        sa.Column("require_human_approval", sa.Boolean(), nullable=False),
        sa.Column("handler_key", sa.String(160), nullable=False),
        sa.Column("random_bytes", sa.Integer(), nullable=False),
        sa.Column("max_failures", sa.Integer(), nullable=False),
        sa.Column("next_rotation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["secret_id"], ["secret_records.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("secret_id", name="uq_secret_rotation_policy_secret"),
        sa.CheckConstraint("rotation_mode IN ('monitor_only','managed_random','external_handler')", name="ck_secret_rotation_policy_mode"),
        sa.CheckConstraint("interval_seconds >= 60", name="ck_secret_rotation_policy_interval"),
        sa.CheckConstraint("warning_seconds >= 0", name="ck_secret_rotation_policy_warning"),
        sa.CheckConstraint("random_bytes >= 16", name="ck_secret_rotation_policy_random_bytes"),
        sa.CheckConstraint("max_failures >= 1", name="ck_secret_rotation_policy_max_failures"),
    )
    op.create_index("ix_secret_rotation_policy_due", "secret_rotation_policies", ["enabled", "next_rotation_at"])
    op.create_index("ix_secret_rotation_policies_secret_id", "secret_rotation_policies", ["secret_id"])
    op.create_index("ix_secret_rotation_policies_workspace_id", "secret_rotation_policies", ["workspace_id"])
    op.create_index("ix_secret_rotation_policies_next_rotation_at", "secret_rotation_policies", ["next_rotation_at"])

    op.create_table(
        "secret_rotation_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("secret_id", sa.String(64), nullable=False),
        sa.Column("policy_id", sa.String(64), nullable=True),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("trigger_type", sa.String(16), nullable=False),
        sa.Column("rotation_mode", sa.String(32), nullable=False),
        sa.Column("handler_key", sa.String(160), nullable=False),
        sa.Column("requested_by", sa.String(255), nullable=False),
        sa.Column("approved_by", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("old_version", sa.Integer(), nullable=False),
        sa.Column("new_version", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["secret_id"], ["secret_records.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["policy_id"], ["secret_rotation_policies.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("idempotency_key", name="uq_secret_rotation_runs_idempotency"),
        sa.CheckConstraint("status IN ('proposed','running','completed','failed','rejected','cancelled')", name="ck_secret_rotation_runs_status"),
        sa.CheckConstraint("trigger_type IN ('manual','scheduled','expiry','health')", name="ck_secret_rotation_runs_trigger"),
    )
    op.create_index("ix_secret_rotation_runs_history", "secret_rotation_runs", ["secret_id", "created_at"])
    op.create_index("ix_secret_rotation_runs_secret_id", "secret_rotation_runs", ["secret_id"])
    op.create_index("ix_secret_rotation_runs_policy_id", "secret_rotation_runs", ["policy_id"])
    op.create_index("ix_secret_rotation_runs_workspace_id", "secret_rotation_runs", ["workspace_id"])
    op.create_index("ix_secret_rotation_runs_status", "secret_rotation_runs", ["status"])
    op.create_index("ix_secret_rotation_runs_created_at", "secret_rotation_runs", ["created_at"])

    op.create_table(
        "secret_health_checks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("secret_id", sa.String(64), nullable=False),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("provider_status", sa.String(16), nullable=False),
        sa.Column("expires_in_seconds", sa.Integer(), nullable=True),
        sa.Column("rotation_due", sa.Boolean(), nullable=False),
        sa.Column("rotation_due_in_seconds", sa.Integer(), nullable=True),
        sa.Column("recent_error_count", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["secret_id"], ["secret_records.id"], ondelete="CASCADE"),
        sa.CheckConstraint("status IN ('healthy','warning','critical','error')", name="ck_secret_health_checks_status"),
    )
    op.create_index("ix_secret_health_checks_history", "secret_health_checks", ["workspace_id", "secret_id", "checked_at"])
    op.create_index("ix_secret_health_checks_secret_id", "secret_health_checks", ["secret_id"])
    op.create_index("ix_secret_health_checks_provider_id", "secret_health_checks", ["provider_id"])
    op.create_index("ix_secret_health_checks_workspace_id", "secret_health_checks", ["workspace_id"])
    op.create_index("ix_secret_health_checks_status", "secret_health_checks", ["status"])
    op.create_index("ix_secret_health_checks_checked_at", "secret_health_checks", ["checked_at"])

    op.create_table(
        "secret_health_alerts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("secret_id", sa.String(64), nullable=False),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("alert_key", sa.String(160), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(255), nullable=False),
        sa.Column("resolution_note", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["secret_id"], ["secret_records.id"], ondelete="CASCADE"),
        sa.CheckConstraint("status IN ('open','resolved','suppressed')", name="ck_secret_health_alerts_status"),
        sa.CheckConstraint("severity IN ('warning','critical')", name="ck_secret_health_alerts_severity"),
    )
    op.create_index("ix_secret_health_alerts_open", "secret_health_alerts", ["workspace_id", "status", "severity", "opened_at"] if False else ["workspace_id", "status", "severity", "first_seen_at"])
    op.create_index("ix_secret_health_alerts_secret_id", "secret_health_alerts", ["secret_id"])
    op.create_index("ix_secret_health_alerts_provider_id", "secret_health_alerts", ["provider_id"])
    op.create_index("ix_secret_health_alerts_workspace_id", "secret_health_alerts", ["workspace_id"])
    op.create_index("ix_secret_health_alerts_alert_key", "secret_health_alerts", ["alert_key"])
    op.create_index("ix_secret_health_alerts_severity", "secret_health_alerts", ["severity"])
    op.create_index("ix_secret_health_alerts_status", "secret_health_alerts", ["status"])


def downgrade():
    op.drop_index("ix_secret_health_alerts_status", table_name="secret_health_alerts")
    op.drop_index("ix_secret_health_alerts_severity", table_name="secret_health_alerts")
    op.drop_index("ix_secret_health_alerts_alert_key", table_name="secret_health_alerts")
    op.drop_index("ix_secret_health_alerts_workspace_id", table_name="secret_health_alerts")
    op.drop_index("ix_secret_health_alerts_provider_id", table_name="secret_health_alerts")
    op.drop_index("ix_secret_health_alerts_secret_id", table_name="secret_health_alerts")
    op.drop_index("ix_secret_health_alerts_open", table_name="secret_health_alerts")
    op.drop_table("secret_health_alerts")

    op.drop_index("ix_secret_health_checks_checked_at", table_name="secret_health_checks")
    op.drop_index("ix_secret_health_checks_status", table_name="secret_health_checks")
    op.drop_index("ix_secret_health_checks_workspace_id", table_name="secret_health_checks")
    op.drop_index("ix_secret_health_checks_provider_id", table_name="secret_health_checks")
    op.drop_index("ix_secret_health_checks_secret_id", table_name="secret_health_checks")
    op.drop_index("ix_secret_health_checks_history", table_name="secret_health_checks")
    op.drop_table("secret_health_checks")

    op.drop_index("ix_secret_rotation_runs_created_at", table_name="secret_rotation_runs")
    op.drop_index("ix_secret_rotation_runs_status", table_name="secret_rotation_runs")
    op.drop_index("ix_secret_rotation_runs_workspace_id", table_name="secret_rotation_runs")
    op.drop_index("ix_secret_rotation_runs_policy_id", table_name="secret_rotation_runs")
    op.drop_index("ix_secret_rotation_runs_secret_id", table_name="secret_rotation_runs")
    op.drop_index("ix_secret_rotation_runs_history", table_name="secret_rotation_runs")
    op.drop_table("secret_rotation_runs")

    op.drop_index("ix_secret_rotation_policies_next_rotation_at", table_name="secret_rotation_policies")
    op.drop_index("ix_secret_rotation_policies_workspace_id", table_name="secret_rotation_policies")
    op.drop_index("ix_secret_rotation_policies_secret_id", table_name="secret_rotation_policies")
    op.drop_index("ix_secret_rotation_policy_due", table_name="secret_rotation_policies")
    op.drop_table("secret_rotation_policies")
