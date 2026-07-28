"""P1-019.9 retention, legal hold and evidence archives.

Revision ID: 20260716_0039
Revises: 20260716_0038
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260716_0039"
down_revision = "20260716_0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "human_control_retention_policies",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scope_key", sa.String(320), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enforcement_mode", sa.String(16), nullable=False, server_default="observe"),
        sa.Column("default_retention_days", sa.Integer(), nullable=False, server_default="365"),
        sa.Column("security_event_retention_days", sa.Integer(), nullable=False, server_default="365"),
        sa.Column("notification_retention_days", sa.Integer(), nullable=False, server_default="180"),
        sa.Column("compliance_report_retention_days", sa.Integer(), nullable=False, server_default="2555"),
        sa.Column("operator_audit_retention_days", sa.Integer(), nullable=False, server_default="2555"),
        sa.Column("archive_before_purge", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("require_human_approval", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("purge_batch_size", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("legal_hold_override_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("scope_key", name="uq_human_control_retention_policies_scope"),
        sa.CheckConstraint("enforcement_mode IN ('observe', 'archive', 'purge')", name="ck_human_control_retention_policies_mode"),
        sa.CheckConstraint("default_retention_days >= 1", name="ck_human_control_retention_policies_default_days"),
        sa.CheckConstraint("purge_batch_size >= 1 AND purge_batch_size <= 10000", name="ck_human_control_retention_policies_batch"),
    )
    for name, columns in (
        ("ix_human_control_retention_policies_workspace_id", ["workspace_id"]),
        ("ix_human_control_retention_policies_enforcement_mode", ["enforcement_mode"]),
        ("ix_human_control_retention_policies_created_at", ["created_at"]),
        ("ix_human_control_retention_policies_effective", ["workspace_id", "enabled"]),
    ):
        op.create_index(name, "human_control_retention_policies", columns, unique=False)

    op.create_table(
        "human_control_legal_holds",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scope_key", sa.String(320), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("hold_key", sa.String(160), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("target_types_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("target_ids_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("event_patterns_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("actor_ids_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("custodian_ids_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("released_by", sa.String(255), nullable=True),
        sa.Column("release_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("scope_key", name="uq_human_control_legal_holds_scope"),
        sa.CheckConstraint("status IN ('active', 'released', 'expired')", name="ck_human_control_legal_holds_status"),
    )
    for name, columns in (
        ("ix_human_control_legal_holds_workspace_id", ["workspace_id"]),
        ("ix_human_control_legal_holds_hold_key", ["hold_key"]),
        ("ix_human_control_legal_holds_status", ["status"]),
        ("ix_human_control_legal_holds_period_start", ["period_start"]),
        ("ix_human_control_legal_holds_period_end", ["period_end"]),
        ("ix_human_control_legal_holds_expires_at", ["expires_at"]),
        ("ix_human_control_legal_holds_created_by", ["created_by"]),
        ("ix_human_control_legal_holds_created_at", ["created_at"]),
        ("ix_human_control_legal_holds_active", ["workspace_id", "status", "expires_at"]),
    ):
        op.create_index(name, "human_control_legal_holds", columns, unique=False)

    op.create_table(
        "human_control_evidence_archives",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scope_key", sa.String(320), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("archive_key", sa.String(160), nullable=False),
        sa.Column("archive_type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="building"),
        sa.Column("legal_hold_id", sa.String(64), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_types_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("manifest_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column("previous_archive_hash", sa.String(64), nullable=False),
        sa.Column("archive_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("sealed_by", sa.String(255), nullable=True),
        sa.Column("revoked_by", sa.String(255), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["legal_hold_id"], ["human_control_legal_holds.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("scope_key", name="uq_human_control_evidence_archives_scope"),
        sa.CheckConstraint("status IN ('building', 'sealed', 'revoked')", name="ck_human_control_evidence_archives_status"),
        sa.CheckConstraint("archive_type IN ('audit', 'compliance', 'legal_hold', 'retention', 'external_audit', 'custom')", name="ck_human_control_evidence_archives_type"),
    )
    for name, columns in (
        ("ix_human_control_evidence_archives_workspace_id", ["workspace_id"]),
        ("ix_human_control_evidence_archives_archive_key", ["archive_key"]),
        ("ix_human_control_evidence_archives_archive_type", ["archive_type"]),
        ("ix_human_control_evidence_archives_status", ["status"]),
        ("ix_human_control_evidence_archives_legal_hold_id", ["legal_hold_id"]),
        ("ix_human_control_evidence_archives_manifest_hash", ["manifest_hash"]),
        ("ix_human_control_evidence_archives_archive_hash", ["archive_hash"]),
        ("ix_human_control_evidence_archives_created_by", ["created_by"]),
        ("ix_human_control_evidence_archives_created_at", ["created_at"]),
        ("ix_human_control_evidence_archives_sealed_at", ["sealed_at"]),
        ("ix_human_control_evidence_archives_history", ["workspace_id", "status", "created_at"]),
    ):
        op.create_index(name, "human_control_evidence_archives", columns, unique=False)

    op.create_table(
        "human_control_evidence_archive_items",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("archive_id", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(96), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["archive_id"], ["human_control_evidence_archives.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("archive_id", "ordinal", name="uq_human_control_evidence_archive_items_ordinal"),
        sa.UniqueConstraint("archive_id", "source_type", "source_id", name="uq_human_control_evidence_archive_items_source"),
    )
    for name, columns in (
        ("ix_human_control_evidence_archive_items_archive_id", ["archive_id"]),
        ("ix_human_control_evidence_archive_items_source_type", ["source_type"]),
        ("ix_human_control_evidence_archive_items_source_id", ["source_id"]),
        ("ix_human_control_evidence_archive_items_occurred_at", ["occurred_at"]),
        ("ix_human_control_evidence_archive_items_content_hash", ["content_hash"]),
        ("ix_human_control_evidence_archive_items_created_at", ["created_at"]),
        ("ix_human_control_evidence_archive_items_lookup", ["archive_id", "source_type", "ordinal"]),
    ):
        op.create_index(name, "human_control_evidence_archive_items", columns, unique=False)

    op.create_table(
        "human_control_retention_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("policy_id", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("run_mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("started_by", sa.String(255), nullable=False),
        sa.Column("cutoff_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("eligible_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("held_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("archived_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("deleted_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("errors_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("evidence_archive_id", sa.String(64), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["policy_id"], ["human_control_retention_policies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["evidence_archive_id"], ["human_control_evidence_archives.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("idempotency_key", name="uq_human_control_retention_runs_idempotency"),
        sa.CheckConstraint("run_mode IN ('preview', 'apply')", name="ck_human_control_retention_runs_mode"),
        sa.CheckConstraint("status IN ('running', 'completed', 'failed')", name="ck_human_control_retention_runs_status"),
    )
    for name, columns in (
        ("ix_human_control_retention_runs_policy_id", ["policy_id"]),
        ("ix_human_control_retention_runs_workspace_id", ["workspace_id"]),
        ("ix_human_control_retention_runs_idempotency_key", ["idempotency_key"]),
        ("ix_human_control_retention_runs_run_mode", ["run_mode"]),
        ("ix_human_control_retention_runs_status", ["status"]),
        ("ix_human_control_retention_runs_started_by", ["started_by"]),
        ("ix_human_control_retention_runs_evidence_archive_id", ["evidence_archive_id"]),
        ("ix_human_control_retention_runs_started_at", ["started_at"]),
        ("ix_human_control_retention_runs_history", ["workspace_id", "status", "started_at"]),
    ):
        op.create_index(name, "human_control_retention_runs", columns, unique=False)

    op.create_table(
        "human_control_external_audit_packages",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scope_key", sa.String(320), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("package_key", sa.String(160), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("auditor_name", sa.String(500), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("archive_ids_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("report_ids_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("manifest_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("package_hash", sa.String(64), nullable=False),
        sa.Column("generated_by", sa.String(255), nullable=False),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(255), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("scope_key", name="uq_human_control_external_audit_packages_scope"),
        sa.CheckConstraint("status IN ('draft', 'sealed', 'revoked', 'expired')", name="ck_human_control_external_audit_packages_status"),
    )
    for name, columns in (
        ("ix_human_control_external_audit_packages_workspace_id", ["workspace_id"]),
        ("ix_human_control_external_audit_packages_package_key", ["package_key"]),
        ("ix_human_control_external_audit_packages_status", ["status"]),
        ("ix_human_control_external_audit_packages_package_hash", ["package_hash"]),
        ("ix_human_control_external_audit_packages_generated_by", ["generated_by"]),
        ("ix_human_control_external_audit_packages_access_expires_at", ["access_expires_at"]),
        ("ix_human_control_external_audit_packages_created_at", ["created_at"]),
        ("ix_human_control_external_audit_packages_history", ["workspace_id", "status", "created_at"]),
    ):
        op.create_index(name, "human_control_external_audit_packages", columns, unique=False)


def downgrade() -> None:
    op.drop_table("human_control_external_audit_packages")
    op.drop_table("human_control_retention_runs")
    op.drop_table("human_control_evidence_archive_items")
    op.drop_table("human_control_evidence_archives")
    op.drop_table("human_control_legal_holds")
    op.drop_table("human_control_retention_policies")
