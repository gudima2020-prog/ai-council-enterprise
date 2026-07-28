"""P1-019.8 immutable operator audit, compliance and access review.

Revision ID: 20260716_0038
Revises: 20260716_0037
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260716_0038"
down_revision = "20260716_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "human_control_operator_audit_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(96), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("event_type", sa.String(255), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=True),
        sa.Column("identity_id", sa.String(64), nullable=True),
        sa.Column("auth_method", sa.String(32), nullable=True),
        sa.Column("action", sa.String(96), nullable=False),
        sa.Column("resource_type", sa.String(96), nullable=True),
        sa.Column("resource_id", sa.String(128), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("risk_level", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("correlation_id", sa.String(96), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_hash", sa.String(64), nullable=False),
        sa.Column("event_hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("sequence", name="uq_human_control_operator_audit_events_sequence"),
        sa.UniqueConstraint("event_id", name="uq_human_control_operator_audit_events_event"),
        sa.CheckConstraint(
            "outcome IN ('success', 'failure', 'unknown')",
            name="ck_human_control_operator_audit_events_outcome",
        ),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="ck_human_control_operator_audit_events_risk",
        ),
    )
    for name, columns in (
        ("ix_human_control_operator_audit_events_sequence", ["sequence"]),
        ("ix_human_control_operator_audit_events_event_id", ["event_id"]),
        ("ix_human_control_operator_audit_events_workspace_id", ["workspace_id"]),
        ("ix_human_control_operator_audit_events_event_type", ["event_type"]),
        ("ix_human_control_operator_audit_events_source", ["source"]),
        ("ix_human_control_operator_audit_events_actor_id", ["actor_id"]),
        ("ix_human_control_operator_audit_events_identity_id", ["identity_id"]),
        ("ix_human_control_operator_audit_events_auth_method", ["auth_method"]),
        ("ix_human_control_operator_audit_events_action", ["action"]),
        ("ix_human_control_operator_audit_events_resource_type", ["resource_type"]),
        ("ix_human_control_operator_audit_events_resource_id", ["resource_id"]),
        ("ix_human_control_operator_audit_events_outcome", ["outcome"]),
        ("ix_human_control_operator_audit_events_risk_level", ["risk_level"]),
        ("ix_human_control_operator_audit_events_correlation_id", ["correlation_id"]),
        ("ix_human_control_operator_audit_events_occurred_at", ["occurred_at"]),
        ("ix_human_control_operator_audit_events_recorded_at", ["recorded_at"]),
        ("ix_human_control_operator_audit_events_event_hash", ["event_hash"]),
        (
            "ix_human_control_operator_audit_events_history",
            ["workspace_id", "actor_id", "event_type", "occurred_at"],
        ),
    ):
        op.create_index(name, "human_control_operator_audit_events", columns, unique=False)

    op.create_table(
        "human_control_compliance_reports",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("report_type", sa.String(32), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_by", sa.String(255), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("report_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "report_type IN ('activity_summary', 'privileged_access', 'authentication', 'approval_governance', 'full')",
            name="ck_human_control_compliance_reports_type",
        ),
    )
    for name, columns in (
        ("ix_human_control_compliance_reports_workspace_id", ["workspace_id"]),
        ("ix_human_control_compliance_reports_report_type", ["report_type"]),
        ("ix_human_control_compliance_reports_generated_by", ["generated_by"]),
        ("ix_human_control_compliance_reports_evidence_hash", ["evidence_hash"]),
        ("ix_human_control_compliance_reports_generated_at", ["generated_at"]),
        (
            "ix_human_control_compliance_reports_history",
            ["workspace_id", "report_type", "generated_at"],
        ),
    ):
        op.create_index(name, "human_control_compliance_reports", columns, unique=False)

    op.create_table(
        "human_control_access_reviews",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("initiated_by", sa.String(255), nullable=False),
        sa.Column("include_expired", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("include_sessions", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("summary_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("completed_by", sa.String(255), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completion_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("idempotency_key", name="uq_human_control_access_reviews_idempotency"),
        sa.CheckConstraint(
            "status IN ('open', 'completed', 'cancelled')",
            name="ck_human_control_access_reviews_status",
        ),
    )
    for name, columns in (
        ("ix_human_control_access_reviews_workspace_id", ["workspace_id"]),
        ("ix_human_control_access_reviews_idempotency_key", ["idempotency_key"]),
        ("ix_human_control_access_reviews_status", ["status"]),
        ("ix_human_control_access_reviews_initiated_by", ["initiated_by"]),
        ("ix_human_control_access_reviews_evidence_hash", ["evidence_hash"]),
        ("ix_human_control_access_reviews_created_at", ["created_at"]),
        (
            "ix_human_control_access_reviews_queue",
            ["workspace_id", "status", "created_at"],
        ),
    ):
        op.create_index(name, "human_control_access_reviews", columns, unique=False)

    op.create_table(
        "human_control_access_review_findings",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("review_id", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=True),
        sa.Column("identity_id", sa.String(64), nullable=True),
        sa.Column("finding_type", sa.String(96), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("resource_type", sa.String(96), nullable=False),
        sa.Column("resource_id", sa.String(128), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("recommended_action", sa.Text(), nullable=False, server_default=""),
        sa.Column("resolution", sa.Text(), nullable=False, server_default=""),
        sa.Column("resolved_by", sa.String(255), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["review_id"], ["human_control_access_reviews.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "review_id",
            "fingerprint",
            name="uq_human_control_access_review_findings_fingerprint",
        ),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_human_control_access_review_findings_severity",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'accepted', 'remediated', 'dismissed')",
            name="ck_human_control_access_review_findings_status",
        ),
    )
    for name, columns in (
        ("ix_human_control_access_review_findings_review_id", ["review_id"]),
        ("ix_human_control_access_review_findings_workspace_id", ["workspace_id"]),
        ("ix_human_control_access_review_findings_fingerprint", ["fingerprint"]),
        ("ix_human_control_access_review_findings_actor_id", ["actor_id"]),
        ("ix_human_control_access_review_findings_identity_id", ["identity_id"]),
        ("ix_human_control_access_review_findings_finding_type", ["finding_type"]),
        ("ix_human_control_access_review_findings_severity", ["severity"]),
        ("ix_human_control_access_review_findings_status", ["status"]),
        ("ix_human_control_access_review_findings_resource_type", ["resource_type"]),
        ("ix_human_control_access_review_findings_resource_id", ["resource_id"]),
        ("ix_human_control_access_review_findings_created_at", ["created_at"]),
        (
            "ix_human_control_access_review_findings_queue",
            ["workspace_id", "status", "severity", "created_at"],
        ),
    ):
        op.create_index(name, "human_control_access_review_findings", columns, unique=False)


def downgrade() -> None:
    op.drop_table("human_control_access_review_findings")
    op.drop_table("human_control_access_reviews")
    op.drop_table("human_control_compliance_reports")
    op.drop_table("human_control_operator_audit_events")
