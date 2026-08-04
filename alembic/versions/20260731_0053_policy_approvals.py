"""P2-012 Policy Approval persistence and decision evidence.

Revision ID: 20260731_0053
Revises: 20260724_0052
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260731_0053"
down_revision = "20260724_0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "policy_approvals",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("policy_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("subject_type", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.String(length=255), nullable=False),
        sa.Column("subject_payload_json", sa.JSON(), nullable=False),
        sa.Column("scope_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.String(length=255), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_note", sa.Text(), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=True),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(length=255), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_note", sa.Text(), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'denied', "
            "'expired', 'revoked', 'consumed')",
            name="ck_policy_approvals_status",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for name in (
        "workspace_id",
        "operation",
        "policy_fingerprint",
        "subject_type",
        "subject_id",
        "scope_fingerprint",
        "status",
        "requested_at",
        "expires_at",
        "created_at",
    ):
        op.create_index(
            f"ix_policy_approvals_{name}",
            "policy_approvals",
            [name],
        )

    op.create_table(
        "policy_approval_evidence",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("approval_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=True),
        sa.Column("approval_status", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_hash", sa.String(length=64), nullable=False),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "approval_status IN ('pending', 'approved', 'denied', "
            "'expired', 'revoked', 'consumed')",
            name="ck_policy_approval_evidence_status",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["policy_approvals.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sequence"),
        sa.UniqueConstraint(
            "event_hash",
            name="uq_policy_approval_evidence_event_hash",
        ),
    )
    for name in (
        "sequence",
        "approval_id",
        "workspace_id",
        "event_type",
        "actor_id",
        "approval_status",
        "occurred_at",
        "event_hash",
    ):
        op.create_index(
            f"ix_policy_approval_evidence_{name}",
            "policy_approval_evidence",
            [name],
        )


def downgrade() -> None:
    op.drop_table("policy_approval_evidence")
    op.drop_table("policy_approvals")
