"""Agent collaboration, messaging, delegation and shared context.

Revision ID: 20260715_0014
Revises: 20260715_0013
Create Date: 2026-07-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0014"
down_revision: Union[str, Sequence[str], None] = "20260715_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_conversations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("topic_key", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_by_agent_id",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
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
            "closed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ('open', 'closed')",
            name="ck_agent_conversations_status",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_agent_id"],
            ["agent_profiles.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["execution_plans.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_id",
            "topic_key",
            name="uq_agent_conversations_plan_topic",
        ),
    )
    op.create_index(
        "ix_agent_conversations_workspace_id",
        "agent_conversations",
        ["workspace_id"],
    )
    op.create_index(
        "ix_agent_conversations_plan_id",
        "agent_conversations",
        ["plan_id"],
    )
    op.create_index(
        "ix_agent_conversations_status",
        "agent_conversations",
        ["status"],
    )
    op.create_index(
        "ix_agent_conversations_created_by_agent_id",
        "agent_conversations",
        ["created_by_agent_id"],
    )
    op.create_index(
        "ix_agent_conversations_created_at",
        "agent_conversations",
        ["created_at"],
    )

    op.create_table(
        "agent_messages",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("sender_agent_id", sa.String(length=64), nullable=True),
        sa.Column("recipient_agent_id", sa.String(length=64), nullable=True),
        sa.Column("message_type", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column(
            "reply_to_message_id",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "read_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "handled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "message_type IN ("
            "'message', 'request', 'response', 'delegation', 'system'"
            ")",
            name="ck_agent_messages_type",
        ),
        sa.CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'critical')",
            name="ck_agent_messages_priority",
        ),
        sa.CheckConstraint(
            "status IN ('sent', 'read', 'handled')",
            name="ck_agent_messages_status",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["agent_conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["execution_plans.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sender_agent_id"],
            ["agent_profiles.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_agent_id"],
            ["agent_profiles.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reply_to_message_id"],
            ["agent_messages.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence",
            name="uq_agent_messages_conversation_sequence",
        ),
    )
    for name in (
        "conversation_id",
        "plan_id",
        "sender_agent_id",
        "recipient_agent_id",
        "message_type",
        "correlation_id",
        "reply_to_message_id",
        "status",
        "created_at",
    ):
        op.create_index(
            f"ix_agent_messages_{name}",
            "agent_messages",
            [name],
        )

    op.create_table(
        "agent_delegations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=True),
        sa.Column("source_step_id", sa.String(length=64), nullable=True),
        sa.Column(
            "parent_delegation_id",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "delegator_agent_id",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("delegate_agent_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("max_depth", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
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
            "accepted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "depth >= 0 AND max_depth >= 0 AND depth <= max_depth",
            name="ck_agent_delegations_depth",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'requested', 'accepted', 'running', 'completed', "
            "'failed', 'rejected', 'cancelled'"
            ")",
            name="ck_agent_delegations_status",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["execution_plans.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["agent_conversations.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_step_id"],
            ["execution_plan_steps.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["parent_delegation_id"],
            ["agent_delegations.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["delegator_agent_id"],
            ["agent_profiles.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["delegate_agent_id"],
            ["agent_profiles.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for name in (
        "workspace_id",
        "plan_id",
        "conversation_id",
        "source_step_id",
        "parent_delegation_id",
        "delegator_agent_id",
        "delegate_agent_id",
        "status",
        "created_at",
    ):
        op.create_index(
            f"ix_agent_delegations_{name}",
            "agent_delegations",
            [name],
        )

    op.create_table(
        "execution_context_entries",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.String(length=128), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("writer_agent_id", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
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
        sa.CheckConstraint(
            "scope_type IN ('plan', 'agent', 'step', 'delegation')",
            name="ck_execution_context_scope_type",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_execution_context_version",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["execution_plans.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["writer_agent_id"],
            ["agent_profiles.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_id",
            "scope_type",
            "scope_id",
            "key",
            name="uq_execution_context_scope_key",
        ),
    )
    for name in (
        "workspace_id",
        "plan_id",
        "scope_type",
        "scope_id",
        "key",
        "writer_agent_id",
        "created_at",
    ):
        op.create_index(
            f"ix_execution_context_entries_{name}",
            "execution_context_entries",
            [name],
        )


def downgrade() -> None:
    for name in (
        "created_at",
        "writer_agent_id",
        "key",
        "scope_id",
        "scope_type",
        "plan_id",
        "workspace_id",
    ):
        op.drop_index(
            f"ix_execution_context_entries_{name}",
            table_name="execution_context_entries",
        )
    op.drop_table("execution_context_entries")

    for name in (
        "created_at",
        "status",
        "delegate_agent_id",
        "delegator_agent_id",
        "parent_delegation_id",
        "source_step_id",
        "conversation_id",
        "plan_id",
        "workspace_id",
    ):
        op.drop_index(
            f"ix_agent_delegations_{name}",
            table_name="agent_delegations",
        )
    op.drop_table("agent_delegations")

    for name in (
        "created_at",
        "status",
        "reply_to_message_id",
        "correlation_id",
        "message_type",
        "recipient_agent_id",
        "sender_agent_id",
        "plan_id",
        "conversation_id",
    ):
        op.drop_index(
            f"ix_agent_messages_{name}",
            table_name="agent_messages",
        )
    op.drop_table("agent_messages")

    for name in (
        "created_at",
        "created_by_agent_id",
        "status",
        "plan_id",
        "workspace_id",
    ):
        op.drop_index(
            f"ix_agent_conversations_{name}",
            table_name="agent_conversations",
        )
    op.drop_table("agent_conversations")
