"""Distributed execution workers, work queue and leases

Revision ID: 20260715_0019
Revises: 20260715_0018
Create Date: 2026-07-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0019"
down_revision: Union[str, Sequence[str], None] = "20260715_0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "execution_workers",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("worker_key", sa.String(length=255), nullable=False),
        sa.Column("instance_id", sa.String(length=255), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("process_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("queues_json", sa.JSON(), nullable=False),
        sa.Column("capabilities_json", sa.JSON(), nullable=False),
        sa.Column("max_concurrency", sa.Integer(), nullable=False),
        sa.Column("active_leases", sa.Integer(), nullable=False),
        sa.Column("heartbeat_ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("default_lease_seconds", sa.Integer(), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("draining_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'draining', 'offline', 'unhealthy')",
            name="ck_execution_workers_status",
        ),
        sa.CheckConstraint(
            "max_concurrency >= 1",
            name="ck_execution_workers_max_concurrency",
        ),
        sa.CheckConstraint(
            "heartbeat_ttl_seconds >= 5",
            name="ck_execution_workers_heartbeat_ttl",
        ),
        sa.CheckConstraint(
            "default_lease_seconds >= 5",
            name="ck_execution_workers_default_lease",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "worker_key",
            name="uq_execution_workers_worker_key",
        ),
    )
    for name in (
        "worker_key",
        "instance_id",
        "status",
        "enabled",
        "last_heartbeat_at",
        "expires_at",
        "created_at",
    ):
        op.create_index(
            f"ix_execution_workers_{name}",
            "execution_workers",
            [name],
        )

    op.create_table(
        "execution_work_items",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("plan_id", sa.String(length=64), nullable=True),
        sa.Column("work_type", sa.String(length=32), nullable=False),
        sa.Column("queue_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("required_capabilities_json", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("worker_id", sa.String(length=64), nullable=True),
        sa.Column("last_lease_token", sa.String(length=255), nullable=True),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "work_type IN ('execution_plan')",
            name="ck_execution_work_items_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'leased', 'completed', 'failed', 'cancelled')",
            name="ck_execution_work_items_status",
        ),
        sa.CheckConstraint(
            "priority >= -1000 AND priority <= 1000",
            name="ck_execution_work_items_priority",
        ),
        sa.CheckConstraint(
            "max_attempts >= 1",
            name="ck_execution_work_items_max_attempts",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_execution_work_items_attempt_count",
        ),
        sa.CheckConstraint(
            "fencing_token >= 0",
            name="ck_execution_work_items_fencing_token",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["execution_plans.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["worker_id"],
            ["execution_workers.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_execution_work_items_idempotency_key",
        ),
    )
    for name in (
        "workspace_id",
        "plan_id",
        "work_type",
        "queue_name",
        "status",
        "priority",
        "available_at",
        "worker_id",
        "last_lease_token",
        "lease_expires_at",
        "created_at",
    ):
        op.create_index(
            f"ix_execution_work_items_{name}",
            "execution_work_items",
            [name],
        )

    op.create_table(
        "execution_leases",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("work_item_id", sa.String(length=64), nullable=False),
        sa.Column("worker_id", sa.String(length=64), nullable=True),
        sa.Column("lease_token", sa.String(length=255), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_reason", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'renewed', 'completed', 'failed', 'released', 'expired', 'lost')",
            name="ck_execution_leases_status",
        ),
        sa.CheckConstraint(
            "fencing_token >= 1",
            name="ck_execution_leases_fencing_token",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["execution_work_items.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["worker_id"],
            ["execution_workers.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "lease_token",
            name="uq_execution_leases_token",
        ),
        sa.UniqueConstraint(
            "work_item_id",
            "fencing_token",
            name="uq_execution_leases_work_fence",
        ),
    )
    for name in (
        "work_item_id",
        "worker_id",
        "lease_token",
        "status",
        "acquired_at",
        "expires_at",
        "created_at",
    ):
        op.create_index(
            f"ix_execution_leases_{name}",
            "execution_leases",
            [name],
        )


def downgrade() -> None:
    for name in reversed(
        (
            "work_item_id",
            "worker_id",
            "lease_token",
            "status",
            "acquired_at",
            "expires_at",
            "created_at",
        )
    ):
        op.drop_index(
            f"ix_execution_leases_{name}",
            table_name="execution_leases",
        )
    op.drop_table("execution_leases")

    for name in reversed(
        (
            "workspace_id",
            "plan_id",
            "work_type",
            "queue_name",
            "status",
            "priority",
            "available_at",
            "worker_id",
            "last_lease_token",
            "lease_expires_at",
            "created_at",
        )
    ):
        op.drop_index(
            f"ix_execution_work_items_{name}",
            table_name="execution_work_items",
        )
    op.drop_table("execution_work_items")

    for name in reversed(
        (
            "worker_key",
            "instance_id",
            "status",
            "enabled",
            "last_heartbeat_at",
            "expires_at",
            "created_at",
        )
    ):
        op.drop_index(
            f"ix_execution_workers_{name}",
            table_name="execution_workers",
        )
    op.drop_table("execution_workers")
