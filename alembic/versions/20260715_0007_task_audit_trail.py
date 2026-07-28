"""Immutable task audit trail

Revision ID: 20260715_0007
Revises: 20260715_0006
Create Date: 2026-07-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0007"
down_revision: Union[str, Sequence[str], None] = "20260715_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_audit_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("approval_id", sa.String(length=64), nullable=True),
        sa.Column(
            "workflow_instance_id",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("previous_hash", sa.String(length=64), nullable=False),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
        sa.UniqueConstraint("event_hash"),
        sa.UniqueConstraint("sequence"),
    )
    op.create_index(
        "ix_task_audit_events_sequence",
        "task_audit_events",
        ["sequence"],
    )
    op.create_index(
        "ix_task_audit_events_event_id",
        "task_audit_events",
        ["event_id"],
    )
    op.create_index(
        "ix_task_audit_events_event_type",
        "task_audit_events",
        ["event_type"],
    )
    op.create_index(
        "ix_task_audit_events_workspace_id",
        "task_audit_events",
        ["workspace_id"],
    )
    op.create_index(
        "ix_task_audit_events_task_id",
        "task_audit_events",
        ["task_id"],
    )
    op.create_index(
        "ix_task_audit_events_approval_id",
        "task_audit_events",
        ["approval_id"],
    )
    op.create_index(
        "ix_task_audit_events_workflow_instance_id",
        "task_audit_events",
        ["workflow_instance_id"],
    )
    op.create_index(
        "ix_task_audit_events_actor_type",
        "task_audit_events",
        ["actor_type"],
    )
    op.create_index(
        "ix_task_audit_events_actor_id",
        "task_audit_events",
        ["actor_id"],
    )
    op.create_index(
        "ix_task_audit_events_occurred_at",
        "task_audit_events",
        ["occurred_at"],
    )
    op.create_index(
        "ix_task_audit_events_recorded_at",
        "task_audit_events",
        ["recorded_at"],
    )
    op.create_index(
        "ix_task_audit_events_event_hash",
        "task_audit_events",
        ["event_hash"],
    )


def downgrade() -> None:
    for index_name in (
        "ix_task_audit_events_event_hash",
        "ix_task_audit_events_recorded_at",
        "ix_task_audit_events_occurred_at",
        "ix_task_audit_events_actor_id",
        "ix_task_audit_events_actor_type",
        "ix_task_audit_events_workflow_instance_id",
        "ix_task_audit_events_approval_id",
        "ix_task_audit_events_task_id",
        "ix_task_audit_events_workspace_id",
        "ix_task_audit_events_event_type",
        "ix_task_audit_events_event_id",
        "ix_task_audit_events_sequence",
    ):
        op.drop_index(index_name, table_name="task_audit_events")

    op.drop_table("task_audit_events")
