"""P1-019.2 operator roles, approval chains and escalations.

Revision ID: 20260716_0032
Revises: 20260716_0031
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260716_0032"
down_revision = "20260716_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "human_control_roles",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("scope_key", sa.String(length=320), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("role_key", sa.String(length=96), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("builtin", sa.Boolean(), nullable=False),
        sa.Column("permissions_json", sa.JSON(), nullable=False),
        sa.Column("allowed_risk_levels_json", sa.JSON(), nullable=False),
        sa.Column("allowed_source_types_json", sa.JSON(), nullable=False),
        sa.Column("allowed_actions_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(role_key) >= 1",
            name="ck_human_control_roles_role_key",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scope_key",
            name="uq_human_control_roles_scope_key",
        ),
    )
    for column in ("workspace_id", "role_key", "created_at"):
        op.create_index(
            f"ix_human_control_roles_{column}",
            "human_control_roles",
            [column],
            unique=False,
        )
    op.create_index(
        "ix_human_control_roles_lookup",
        "human_control_roles",
        ["workspace_id", "role_key", "enabled"],
        unique=False,
    )

    op.create_table(
        "human_control_role_bindings",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("binding_key", sa.String(length=512), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("role_id", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("granted_by", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["human_control_roles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "binding_key",
            name="uq_human_control_role_bindings_key",
        ),
    )
    for column in (
        "workspace_id",
        "actor_id",
        "role_id",
        "expires_at",
        "created_at",
    ):
        op.create_index(
            f"ix_human_control_role_bindings_{column}",
            "human_control_role_bindings",
            [column],
            unique=False,
        )
    op.create_index(
        "ix_human_control_role_bindings_effective",
        "human_control_role_bindings",
        ["workspace_id", "actor_id", "enabled", "expires_at"],
        unique=False,
    )

    op.create_table(
        "human_control_approval_policies",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("scope_key", sa.String(length=320), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("policy_key", sa.String(length=96), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("source_types_json", sa.JSON(), nullable=False),
        sa.Column("action_kinds_json", sa.JSON(), nullable=False),
        sa.Column("risk_levels_json", sa.JSON(), nullable=False),
        sa.Column("decision_actions_json", sa.JSON(), nullable=False),
        sa.Column("steps_json", sa.JSON(), nullable=False),
        sa.Column("distinct_approvers", sa.Boolean(), nullable=False),
        sa.Column(
            "prohibit_source_requester_approval",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "prohibit_case_initiator_approval",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column("rejection_mode", sa.String(length=16), nullable=False),
        sa.Column("case_ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("default_step_ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("escalation_after_seconds", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "priority >= 0 AND priority <= 100",
            name="ck_human_control_approval_policies_priority",
        ),
        sa.CheckConstraint(
            "rejection_mode IN ('any', 'majority')",
            name="ck_human_control_approval_policies_rejection_mode",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scope_key",
            name="uq_human_control_approval_policies_scope_key",
        ),
    )
    for column in ("workspace_id", "policy_key", "created_at"):
        op.create_index(
            f"ix_human_control_approval_policies_{column}",
            "human_control_approval_policies",
            [column],
            unique=False,
        )
    op.create_index(
        "ix_human_control_approval_policies_match",
        "human_control_approval_policies",
        ["workspace_id", "enabled", "priority"],
        unique=False,
    )

    op.create_table(
        "human_control_approval_cases",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("item_id", sa.String(length=64), nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=True),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("requested_action", sa.String(length=64), nullable=False),
        sa.Column("requested_by", sa.String(length=255), nullable=False),
        sa.Column("source_requester", sa.String(length=255), nullable=True),
        sa.Column(
            "request_idempotency_key",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_step_sequence", sa.Integer(), nullable=False),
        sa.Column("total_steps", sa.Integer(), nullable=False),
        sa.Column("distinct_approvers", sa.Boolean(), nullable=False),
        sa.Column(
            "prohibit_source_requester_approval",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "prohibit_case_initiator_approval",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("final_result_json", sa.JSON(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=255), nullable=True),
        sa.Column("resolution_reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'executing', 'executed', "
            "'execution_failed', 'rejected', 'expired', 'cancelled')",
            name="ck_human_control_approval_cases_status",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["human_control_items.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["human_control_approval_policies.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_idempotency_key",
            name="uq_human_control_approval_cases_idempotency",
        ),
    )
    for column in (
        "item_id",
        "policy_id",
        "workspace_id",
        "requested_action",
        "requested_by",
        "source_requester",
        "request_idempotency_key",
        "status",
        "due_at",
        "created_at",
    ):
        op.create_index(
            f"ix_human_control_approval_cases_{column}",
            "human_control_approval_cases",
            [column],
            unique=False,
        )
    op.create_index(
        "ix_human_control_approval_cases_queue",
        "human_control_approval_cases",
        ["workspace_id", "status", "due_at", "created_at"],
        unique=False,
    )

    op.create_table(
        "human_control_approval_steps",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("step_key", sa.String(length=96), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("required_role_keys_json", sa.JSON(), nullable=False),
        sa.Column("escalation_role_keys_json", sa.JSON(), nullable=False),
        sa.Column("min_approvals", sa.Integer(), nullable=False),
        sa.Column("approvals_received", sa.Integer(), nullable=False),
        sa.Column("rejections_received", sa.Integer(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalated", sa.Boolean(), nullable=False),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'skipped', 'expired')",
            name="ck_human_control_approval_steps_status",
        ),
        sa.CheckConstraint(
            "min_approvals >= 1",
            name="ck_human_control_approval_steps_min_approvals",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["human_control_approval_cases.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "case_id",
            "sequence",
            name="uq_human_control_approval_steps_sequence",
        ),
        sa.UniqueConstraint(
            "case_id",
            "step_key",
            name="uq_human_control_approval_steps_key",
        ),
    )
    for column in ("case_id", "sequence", "status", "due_at"):
        op.create_index(
            f"ix_human_control_approval_steps_{column}",
            "human_control_approval_steps",
            [column],
            unique=False,
        )
    op.create_index(
        "ix_human_control_approval_steps_active",
        "human_control_approval_steps",
        ["case_id", "status", "sequence", "due_at"],
        unique=False,
    )

    op.create_table(
        "human_control_approval_votes",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("step_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("role_keys_json", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('approve', 'reject', 'abstain')",
            name="ck_human_control_approval_votes_decision",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["human_control_approval_cases.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["step_id"],
            ["human_control_approval_steps.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_human_control_approval_votes_idempotency",
        ),
        sa.UniqueConstraint(
            "step_id",
            "actor_id",
            name="uq_human_control_approval_votes_actor_step",
        ),
    )
    for column in (
        "case_id",
        "step_id",
        "actor_id",
        "decision",
        "idempotency_key",
        "created_at",
    ):
        op.create_index(
            f"ix_human_control_approval_votes_{column}",
            "human_control_approval_votes",
            [column],
            unique=False,
        )
    op.create_index(
        "ix_human_control_approval_votes_history",
        "human_control_approval_votes",
        ["case_id", "step_id", "actor_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "human_control_escalations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("step_id", sa.String(length=64), nullable=True),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("escalation_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_role_keys_json", sa.JSON(), nullable=False),
        sa.Column("target_role_keys_json", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("assigned_to", sa.String(length=255), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(length=255), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=255), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved', 'cancelled')",
            name="ck_human_control_escalations_status",
        ),
        sa.CheckConstraint(
            "escalation_type IN ('timeout', 'manual', 'critical', 'unassigned')",
            name="ck_human_control_escalations_type",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["human_control_approval_cases.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["step_id"],
            ["human_control_approval_steps.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "case_id",
        "step_id",
        "workspace_id",
        "escalation_type",
        "status",
        "assigned_to",
        "due_at",
        "created_at",
    ):
        op.create_index(
            f"ix_human_control_escalations_{column}",
            "human_control_escalations",
            [column],
            unique=False,
        )
    op.create_index(
        "ix_human_control_escalations_queue",
        "human_control_escalations",
        ["workspace_id", "status", "due_at", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_human_control_escalations_queue",
        table_name="human_control_escalations",
    )
    for column in reversed(
        (
            "case_id",
            "step_id",
            "workspace_id",
            "escalation_type",
            "status",
            "assigned_to",
            "due_at",
            "created_at",
        )
    ):
        op.drop_index(
            f"ix_human_control_escalations_{column}",
            table_name="human_control_escalations",
        )
    op.drop_table("human_control_escalations")

    op.drop_index(
        "ix_human_control_approval_votes_history",
        table_name="human_control_approval_votes",
    )
    for column in reversed(
        (
            "case_id",
            "step_id",
            "actor_id",
            "decision",
            "idempotency_key",
            "created_at",
        )
    ):
        op.drop_index(
            f"ix_human_control_approval_votes_{column}",
            table_name="human_control_approval_votes",
        )
    op.drop_table("human_control_approval_votes")

    op.drop_index(
        "ix_human_control_approval_steps_active",
        table_name="human_control_approval_steps",
    )
    for column in reversed(("case_id", "sequence", "status", "due_at")):
        op.drop_index(
            f"ix_human_control_approval_steps_{column}",
            table_name="human_control_approval_steps",
        )
    op.drop_table("human_control_approval_steps")

    op.drop_index(
        "ix_human_control_approval_cases_queue",
        table_name="human_control_approval_cases",
    )
    for column in reversed(
        (
            "item_id",
            "policy_id",
            "workspace_id",
            "requested_action",
            "requested_by",
            "source_requester",
            "request_idempotency_key",
            "status",
            "due_at",
            "created_at",
        )
    ):
        op.drop_index(
            f"ix_human_control_approval_cases_{column}",
            table_name="human_control_approval_cases",
        )
    op.drop_table("human_control_approval_cases")

    op.drop_index(
        "ix_human_control_approval_policies_match",
        table_name="human_control_approval_policies",
    )
    for column in reversed(("workspace_id", "policy_key", "created_at")):
        op.drop_index(
            f"ix_human_control_approval_policies_{column}",
            table_name="human_control_approval_policies",
        )
    op.drop_table("human_control_approval_policies")

    op.drop_index(
        "ix_human_control_role_bindings_effective",
        table_name="human_control_role_bindings",
    )
    for column in reversed(
        ("workspace_id", "actor_id", "role_id", "expires_at", "created_at")
    ):
        op.drop_index(
            f"ix_human_control_role_bindings_{column}",
            table_name="human_control_role_bindings",
        )
    op.drop_table("human_control_role_bindings")

    op.drop_index(
        "ix_human_control_roles_lookup",
        table_name="human_control_roles",
    )
    for column in reversed(("workspace_id", "role_key", "created_at")):
        op.drop_index(
            f"ix_human_control_roles_{column}",
            table_name="human_control_roles",
        )
    op.drop_table("human_control_roles")
