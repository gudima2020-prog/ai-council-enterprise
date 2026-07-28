"""Task Dead Letter Queue and replay history

Revision ID: 20260715_0009
Revises: 20260715_0008
Create Date: 2026-07-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0009"
down_revision: Union[str, Sequence[str], None] = "20260715_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_dead_letter_entries",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("error_type", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("source_event_type", sa.String(length=255), nullable=True),
        sa.Column("task_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("failure_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("replay_count", sa.Integer(), nullable=False),
        sa.Column("last_replayed_task_id", sa.String(length=64), nullable=True),
        sa.Column("resolved_by", sa.String(length=255), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ('open', 'replayed', 'resolved', 'discarded')",
            name="ck_task_dead_letter_entries_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_task_dead_letter_entries_task_id",
        "task_dead_letter_entries",
        ["task_id"],
    )
    op.create_index(
        "ix_task_dead_letter_entries_workspace_id",
        "task_dead_letter_entries",
        ["workspace_id"],
    )
    op.create_index(
        "ix_task_dead_letter_entries_status",
        "task_dead_letter_entries",
        ["status"],
    )
    op.create_index(
        "ix_task_dead_letter_entries_reason_code",
        "task_dead_letter_entries",
        ["reason_code"],
    )
    op.create_index(
        "ix_task_dead_letter_entries_source_event_type",
        "task_dead_letter_entries",
        ["source_event_type"],
    )
    op.create_index(
        "ix_task_dead_letter_entries_last_replayed_task_id",
        "task_dead_letter_entries",
        ["last_replayed_task_id"],
    )
    op.create_index(
        "ix_task_dead_letter_entries_created_at",
        "task_dead_letter_entries",
        ["created_at"],
    )

    op.create_table(
        "task_dead_letter_replays",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("dead_letter_id", sa.String(length=64), nullable=False),
        sa.Column("source_task_id", sa.String(length=64), nullable=False),
        sa.Column("replay_task_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ("
            "'created', 'waiting', 'enqueued', 'running', "
            "'completed', 'failed', 'cancelled', 'skipped'"
            ")",
            name="ck_task_dead_letter_replays_status",
        ),
        sa.ForeignKeyConstraint(
            ["dead_letter_id"],
            ["task_dead_letter_entries.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dead_letter_id",
            "idempotency_key",
            name="uq_task_dead_letter_replay_idempotency",
        ),
    )
    op.create_index(
        "ix_task_dead_letter_replays_dead_letter_id",
        "task_dead_letter_replays",
        ["dead_letter_id"],
    )
    op.create_index(
        "ix_task_dead_letter_replays_source_task_id",
        "task_dead_letter_replays",
        ["source_task_id"],
    )
    op.create_index(
        "ix_task_dead_letter_replays_replay_task_id",
        "task_dead_letter_replays",
        ["replay_task_id"],
    )
    op.create_index(
        "ix_task_dead_letter_replays_status",
        "task_dead_letter_replays",
        ["status"],
    )
    op.create_index(
        "ix_task_dead_letter_replays_actor_id",
        "task_dead_letter_replays",
        ["actor_id"],
    )
    op.create_index(
        "ix_task_dead_letter_replays_created_at",
        "task_dead_letter_replays",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_task_dead_letter_replays_created_at",
        table_name="task_dead_letter_replays",
    )
    op.drop_index(
        "ix_task_dead_letter_replays_actor_id",
        table_name="task_dead_letter_replays",
    )
    op.drop_index(
        "ix_task_dead_letter_replays_status",
        table_name="task_dead_letter_replays",
    )
    op.drop_index(
        "ix_task_dead_letter_replays_replay_task_id",
        table_name="task_dead_letter_replays",
    )
    op.drop_index(
        "ix_task_dead_letter_replays_source_task_id",
        table_name="task_dead_letter_replays",
    )
    op.drop_index(
        "ix_task_dead_letter_replays_dead_letter_id",
        table_name="task_dead_letter_replays",
    )
    op.drop_table("task_dead_letter_replays")

    op.drop_index(
        "ix_task_dead_letter_entries_created_at",
        table_name="task_dead_letter_entries",
    )
    op.drop_index(
        "ix_task_dead_letter_entries_last_replayed_task_id",
        table_name="task_dead_letter_entries",
    )
    op.drop_index(
        "ix_task_dead_letter_entries_source_event_type",
        table_name="task_dead_letter_entries",
    )
    op.drop_index(
        "ix_task_dead_letter_entries_reason_code",
        table_name="task_dead_letter_entries",
    )
    op.drop_index(
        "ix_task_dead_letter_entries_status",
        table_name="task_dead_letter_entries",
    )
    op.drop_index(
        "ix_task_dead_letter_entries_workspace_id",
        table_name="task_dead_letter_entries",
    )
    op.drop_index(
        "ix_task_dead_letter_entries_task_id",
        table_name="task_dead_letter_entries",
    )
    op.drop_table("task_dead_letter_entries")
