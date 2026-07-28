"""Durable execution event transport and remote executor sessions

Revision ID: 20260715_0020
Revises: 20260715_0019
Create Date: 2026-07-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0020"
down_revision: Union[str, Sequence[str], None] = "20260715_0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "execution_remote_sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("worker_id", sa.String(length=64), nullable=True),
        sa.Column("instance_id", sa.String(length=255), nullable=False),
        sa.Column("protocol_version", sa.String(length=32), nullable=False),
        sa.Column("features_json", sa.JSON(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'closed', 'expired', 'revoked')",
            name="ck_execution_remote_sessions_status",
        ),
        sa.ForeignKeyConstraint(
            ["worker_id"],
            ["execution_workers.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for name in (
        "worker_id",
        "instance_id",
        "protocol_version",
        "status",
        "last_seen_at",
        "expires_at",
        "created_at",
    ):
        op.create_index(
            f"ix_execution_remote_sessions_{name}",
            "execution_remote_sessions",
            [name],
        )

    op.create_table(
        "execution_event_envelopes",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("topic", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=255), nullable=False),
        sa.Column("source_node", sa.String(length=255), nullable=False),
        sa.Column("target_worker_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("headers_json", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_session_id", sa.String(length=64), nullable=True),
        sa.Column("lease_token", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("ack_result_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'leased', 'acknowledged', 'dead', 'cancelled')",
            name="ck_execution_event_envelopes_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_execution_event_envelopes_attempt_count",
        ),
        sa.CheckConstraint(
            "max_attempts >= 1",
            name="ck_execution_event_envelopes_max_attempts",
        ),
        sa.CheckConstraint(
            "priority >= -1000 AND priority <= 1000",
            name="ck_execution_event_envelopes_priority",
        ),
        sa.ForeignKeyConstraint(
            ["target_worker_id"],
            ["execution_workers.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["lease_session_id"],
            ["execution_remote_sessions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id",
            name="uq_execution_event_envelopes_event_id",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_execution_event_envelopes_idempotency_key",
        ),
    )
    for name in (
        "event_id",
        "topic",
        "event_type",
        "source_node",
        "target_worker_id",
        "status",
        "priority",
        "available_at",
        "lease_session_id",
        "lease_token",
        "lease_expires_at",
        "created_at",
        "dead_lettered_at",
    ):
        op.create_index(
            f"ix_execution_event_envelopes_{name}",
            "execution_event_envelopes",
            [name],
        )

    op.create_table(
        "execution_event_receipts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("envelope_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("consumer_key", sa.String(length=255), nullable=False),
        sa.Column("delivery_attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('processed', 'failed')",
            name="ck_execution_event_receipts_status",
        ),
        sa.CheckConstraint(
            "delivery_attempt >= 1",
            name="ck_execution_event_receipts_attempt",
        ),
        sa.ForeignKeyConstraint(
            ["envelope_id"],
            ["execution_event_envelopes.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["execution_remote_sessions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "envelope_id",
            "consumer_key",
            "delivery_attempt",
            name="uq_execution_event_receipts_delivery",
        ),
    )
    for name in (
        "envelope_id",
        "session_id",
        "consumer_key",
        "status",
        "created_at",
    ):
        op.create_index(
            f"ix_execution_event_receipts_{name}",
            "execution_event_receipts",
            [name],
        )


def downgrade() -> None:
    for name in reversed(
        (
            "envelope_id",
            "session_id",
            "consumer_key",
            "status",
            "created_at",
        )
    ):
        op.drop_index(
            f"ix_execution_event_receipts_{name}",
            table_name="execution_event_receipts",
        )
    op.drop_table("execution_event_receipts")

    for name in reversed(
        (
            "event_id",
            "topic",
            "event_type",
            "source_node",
            "target_worker_id",
            "status",
            "priority",
            "available_at",
            "lease_session_id",
            "lease_token",
            "lease_expires_at",
            "created_at",
            "dead_lettered_at",
        )
    ):
        op.drop_index(
            f"ix_execution_event_envelopes_{name}",
            table_name="execution_event_envelopes",
        )
    op.drop_table("execution_event_envelopes")

    for name in reversed(
        (
            "worker_id",
            "instance_id",
            "protocol_version",
            "status",
            "last_seen_at",
            "expires_at",
            "created_at",
        )
    ):
        op.drop_index(
            f"ix_execution_remote_sessions_{name}",
            table_name="execution_remote_sessions",
        )
    op.drop_table("execution_remote_sessions")
