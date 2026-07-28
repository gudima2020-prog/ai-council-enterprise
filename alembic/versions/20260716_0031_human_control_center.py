"""P1-019.1 unified human control center.

Revision ID: 20260716_0031
Revises: 20260716_0030
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260716_0031"
down_revision = "20260716_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "human_control_items",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("source_status", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("action_kind", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("requires_human", sa.Boolean(), nullable=False),
        sa.Column("decision_options_json", sa.JSON(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned_to", sa.String(length=255), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.String(length=64), nullable=True),
        sa.Column("resolved_by", sa.String(length=255), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'snoozed', 'resolved', "
            "'expired', 'superseded')",
            name="ck_human_control_items_status",
        ),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="ck_human_control_items_risk",
        ),
        sa.CheckConstraint(
            "priority >= 0 AND priority <= 100",
            name="ck_human_control_items_priority",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_type",
            "source_id",
            name="uq_human_control_items_source",
        ),
    )
    for column in (
        "workspace_id",
        "source_type",
        "source_id",
        "source_status",
        "status",
        "action_kind",
        "risk_level",
        "priority",
        "due_at",
        "expires_at",
        "assigned_to",
        "claim_expires_at",
        "snoozed_until",
        "resolution",
        "resolved_by",
        "resolved_at",
        "created_at",
    ):
        op.create_index(
            f"ix_human_control_items_{column}",
            "human_control_items",
            [column],
            unique=False,
        )
    op.create_index(
        "ix_human_control_items_inbox",
        "human_control_items",
        ["workspace_id", "status", "risk_level", "priority", "created_at"],
        unique=False,
    )

    op.create_table(
        "human_control_actions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("item_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("requested_action", sa.String(length=64), nullable=True),
        sa.Column("previous_status", sa.String(length=32), nullable=True),
        sa.Column("new_status", sa.String(length=32), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action_type IN ('sync', 'claim', 'release', 'snooze', "
            "'decision', 'source_resolved')",
            name="ck_human_control_actions_type",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["human_control_items.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_human_control_actions_idempotency",
        ),
    )
    for column in (
        "item_id",
        "workspace_id",
        "action_type",
        "actor_id",
        "idempotency_key",
        "requested_action",
        "created_at",
    ):
        op.create_index(
            f"ix_human_control_actions_{column}",
            "human_control_actions",
            [column],
            unique=False,
        )
    op.create_index(
        "ix_human_control_actions_history",
        "human_control_actions",
        ["workspace_id", "item_id", "actor_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_human_control_actions_history",
        table_name="human_control_actions",
    )
    for column in reversed(
        (
            "item_id",
            "workspace_id",
            "action_type",
            "actor_id",
            "idempotency_key",
            "requested_action",
            "created_at",
        )
    ):
        op.drop_index(
            f"ix_human_control_actions_{column}",
            table_name="human_control_actions",
        )
    op.drop_table("human_control_actions")

    op.drop_index(
        "ix_human_control_items_inbox",
        table_name="human_control_items",
    )
    for column in reversed(
        (
            "workspace_id",
            "source_type",
            "source_id",
            "source_status",
            "status",
            "action_kind",
            "risk_level",
            "priority",
            "due_at",
            "expires_at",
            "assigned_to",
            "claim_expires_at",
            "snoozed_until",
            "resolution",
            "resolved_by",
            "resolved_at",
            "created_at",
        )
    ):
        op.drop_index(
            f"ix_human_control_items_{column}",
            table_name="human_control_items",
        )
    op.drop_table("human_control_items")
