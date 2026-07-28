"""P1-019.6 operator notification channels and acknowledgement tracking.

Revision ID: 20260716_0036
Revises: 20260716_0035
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260716_0036"
down_revision = "20260716_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "human_control_notification_channels",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scope_key", sa.String(320), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("channel_key", sa.String(160), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("channel_type", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("endpoint_url", sa.Text(), nullable=True),
        sa.Column("credential_ref", sa.String(500), nullable=True),
        sa.Column("config_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("retry_base_seconds", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("default_ack_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("default_ack_timeout_seconds", sa.Integer(), nullable=False, server_default="1800"),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("scope_key", name="uq_human_control_notification_channels_scope"),
        sa.CheckConstraint(
            "channel_type IN ('in_app', 'webhook', 'log', 'email', 'slack', 'telegram', 'custom')",
            name="ck_human_control_notification_channels_type",
        ),
        sa.CheckConstraint(
            "timeout_seconds >= 1 AND timeout_seconds <= 300",
            name="ck_human_control_notification_channels_timeout",
        ),
        sa.CheckConstraint(
            "max_attempts >= 1 AND max_attempts <= 20",
            name="ck_human_control_notification_channels_attempts",
        ),
    )
    op.create_index("ix_human_control_notification_channels_workspace_id", "human_control_notification_channels", ["workspace_id"], unique=False)
    op.create_index("ix_human_control_notification_channels_channel_key", "human_control_notification_channels", ["channel_key"], unique=False)
    op.create_index("ix_human_control_notification_channels_channel_type", "human_control_notification_channels", ["channel_type"], unique=False)
    op.create_index("ix_human_control_notification_channels_created_at", "human_control_notification_channels", ["created_at"], unique=False)
    op.create_index("ix_human_control_notification_channels_lookup", "human_control_notification_channels", ["workspace_id", "enabled", "channel_type"], unique=False)

    op.create_table(
        "human_control_notification_subscriptions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scope_key", sa.String(320), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("subscription_key", sa.String(160), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("channel_id", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=True),
        sa.Column("role_keys_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("event_patterns_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("source_types_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("risk_levels_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("min_priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("ack_required", sa.Boolean(), nullable=True),
        sa.Column("ack_timeout_seconds", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["channel_id"], ["human_control_notification_channels.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("scope_key", name="uq_human_control_notification_subscriptions_scope"),
        sa.CheckConstraint(
            "min_priority >= 0 AND min_priority <= 100",
            name="ck_human_control_notification_subscriptions_priority",
        ),
    )
    op.create_index("ix_human_control_notification_subscriptions_workspace_id", "human_control_notification_subscriptions", ["workspace_id"], unique=False)
    op.create_index("ix_human_control_notification_subscriptions_subscription_key", "human_control_notification_subscriptions", ["subscription_key"], unique=False)
    op.create_index("ix_human_control_notification_subscriptions_channel_id", "human_control_notification_subscriptions", ["channel_id"], unique=False)
    op.create_index("ix_human_control_notification_subscriptions_actor_id", "human_control_notification_subscriptions", ["actor_id"], unique=False)
    op.create_index("ix_human_control_notification_subscriptions_created_at", "human_control_notification_subscriptions", ["created_at"], unique=False)
    op.create_index("ix_human_control_notification_subscriptions_match", "human_control_notification_subscriptions", ["workspace_id", "enabled", "channel_id"], unique=False)

    op.create_table(
        "human_control_notifications",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("item_id", sa.String(64), nullable=True),
        sa.Column("channel_id", sa.String(64), nullable=True),
        sa.Column("subscription_id", sa.String(64), nullable=True),
        sa.Column("recipient_actor_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(96), nullable=True),
        sa.Column("source_id", sa.String(128), nullable=True),
        sa.Column("severity", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("ack_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ack_status", sa.String(32), nullable=False, server_default="not_required"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivery_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ack_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(255), nullable=True),
        sa.Column("acknowledgement_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["item_id"], ["human_control_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["channel_id"], ["human_control_notification_channels.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["subscription_id"], ["human_control_notification_subscriptions.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("idempotency_key", name="uq_human_control_notifications_idempotency"),
        sa.CheckConstraint(
            "status IN ('pending', 'delivering', 'retrying', 'delivered', 'acknowledged', 'failed', 'cancelled', 'expired')",
            name="ck_human_control_notifications_status",
        ),
        sa.CheckConstraint(
            "ack_status IN ('not_required', 'pending', 'acknowledged', 'overdue')",
            name="ck_human_control_notifications_ack_status",
        ),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_human_control_notifications_severity",
        ),
        sa.CheckConstraint(
            "priority >= 0 AND priority <= 100",
            name="ck_human_control_notifications_priority",
        ),
    )
    for name, columns in (
        ("ix_human_control_notifications_workspace_id", ["workspace_id"]),
        ("ix_human_control_notifications_item_id", ["item_id"]),
        ("ix_human_control_notifications_channel_id", ["channel_id"]),
        ("ix_human_control_notifications_subscription_id", ["subscription_id"]),
        ("ix_human_control_notifications_recipient_actor_id", ["recipient_actor_id"]),
        ("ix_human_control_notifications_event_type", ["event_type"]),
        ("ix_human_control_notifications_source_type", ["source_type"]),
        ("ix_human_control_notifications_source_id", ["source_id"]),
        ("ix_human_control_notifications_severity", ["severity"]),
        ("ix_human_control_notifications_priority", ["priority"]),
        ("ix_human_control_notifications_idempotency_key", ["idempotency_key"]),
        ("ix_human_control_notifications_status", ["status"]),
        ("ix_human_control_notifications_ack_status", ["ack_status"]),
        ("ix_human_control_notifications_available_at", ["available_at"]),
        ("ix_human_control_notifications_delivery_started_at", ["delivery_started_at"]),
        ("ix_human_control_notifications_delivered_at", ["delivered_at"]),
        ("ix_human_control_notifications_ack_due_at", ["ack_due_at"]),
        ("ix_human_control_notifications_acknowledged_at", ["acknowledged_at"]),
        ("ix_human_control_notifications_acknowledged_by", ["acknowledged_by"]),
        ("ix_human_control_notifications_expires_at", ["expires_at"]),
        ("ix_human_control_notifications_created_at", ["created_at"]),
        ("ix_human_control_notifications_queue", ["status", "available_at", "priority", "created_at"]),
        ("ix_human_control_notifications_recipient", ["workspace_id", "recipient_actor_id", "status", "created_at"]),
    ):
        op.create_index(name, "human_control_notifications", columns, unique=False)

    op.create_table(
        "human_control_notification_attempts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("notification_id", sa.String(64), nullable=False),
        sa.Column("channel_id", sa.String(64), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("adapter", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column("response_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["notification_id"], ["human_control_notifications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["channel_id"], ["human_control_notification_channels.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("notification_id", "attempt_number", name="uq_human_control_notification_attempts_number"),
        sa.CheckConstraint(
            "status IN ('started', 'delivered', 'failed')",
            name="ck_human_control_notification_attempts_status",
        ),
    )
    op.create_index("ix_human_control_notification_attempts_notification_id", "human_control_notification_attempts", ["notification_id"], unique=False)
    op.create_index("ix_human_control_notification_attempts_channel_id", "human_control_notification_attempts", ["channel_id"], unique=False)
    op.create_index("ix_human_control_notification_attempts_status", "human_control_notification_attempts", ["status"], unique=False)
    op.create_index("ix_human_control_notification_attempts_started_at", "human_control_notification_attempts", ["started_at"], unique=False)
    op.create_index("ix_human_control_notification_attempts_history", "human_control_notification_attempts", ["notification_id", "attempt_number", "started_at"], unique=False)

    op.create_table(
        "human_control_notification_receipts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("notification_id", sa.String(64), nullable=False),
        sa.Column("receipt_type", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["notification_id"], ["human_control_notifications.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("idempotency_key", name="uq_human_control_notification_receipts_idempotency"),
        sa.CheckConstraint(
            "receipt_type IN ('delivered', 'read', 'acknowledged', 'overdue')",
            name="ck_human_control_notification_receipts_type",
        ),
    )
    op.create_index("ix_human_control_notification_receipts_notification_id", "human_control_notification_receipts", ["notification_id"], unique=False)
    op.create_index("ix_human_control_notification_receipts_receipt_type", "human_control_notification_receipts", ["receipt_type"], unique=False)
    op.create_index("ix_human_control_notification_receipts_actor_id", "human_control_notification_receipts", ["actor_id"], unique=False)
    op.create_index("ix_human_control_notification_receipts_idempotency_key", "human_control_notification_receipts", ["idempotency_key"], unique=False)
    op.create_index("ix_human_control_notification_receipts_created_at", "human_control_notification_receipts", ["created_at"], unique=False)
    op.create_index("ix_human_control_notification_receipts_history", "human_control_notification_receipts", ["notification_id", "receipt_type", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_table("human_control_notification_receipts")
    op.drop_table("human_control_notification_attempts")
    op.drop_table("human_control_notifications")
    op.drop_table("human_control_notification_subscriptions")
    op.drop_table("human_control_notification_channels")
