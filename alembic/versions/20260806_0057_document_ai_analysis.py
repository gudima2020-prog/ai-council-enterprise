"""P3-001.5b governed document AI analysis persistence.

Revision ID: 20260806_0057
Revises: 20260805_0056
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260806_0057"
down_revision = "20260805_0056"
branch_labels = None
depends_on = None


def _indexes(table: str, columns: tuple[str, ...]) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column])


def upgrade() -> None:
    op.create_table(
        "document_ai_analysis_runs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("workflow", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("selection_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("selected_sources_json", sa.JSON(), nullable=False),
        sa.Column("context_manifest_json", sa.JSON(), nullable=False),
        sa.Column("context_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "effective_classification",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("primary_provider", sa.String(length=64), nullable=False),
        sa.Column("primary_model", sa.String(length=255), nullable=False),
        sa.Column("primary_request_id", sa.String(length=64), nullable=False),
        sa.Column("reviewer_provider", sa.String(length=64), nullable=False),
        sa.Column("reviewer_model", sa.String(length=255), nullable=False),
        sa.Column("reviewer_request_id", sa.String(length=64), nullable=False),
        sa.Column("provider_trust_json", sa.JSON(), nullable=False),
        sa.Column(
            "external_provider_acknowledged",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column("request_text", sa.Text(), nullable=True),
        sa.Column("request_text_sha256", sa.String(length=64), nullable=True),
        sa.Column("output_text", sa.Text(), nullable=True),
        sa.Column("output_text_sha256", sa.String(length=64), nullable=True),
        sa.Column("output_citation_ids_json", sa.JSON(), nullable=False),
        sa.Column("content_state", sa.String(length=32), nullable=False),
        sa.Column("content_purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewer_verdict_json", sa.JSON(), nullable=False),
        sa.Column(
            "reviewer_response_sha256",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("runtime_policy_json", sa.JSON(), nullable=False),
        sa.Column("approval_evidence_json", sa.JSON(), nullable=False),
        sa.Column("provider_evidence_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        sa.Column("error_details_json", sa.JSON(), nullable=False),
        sa.Column("retention_policy", sa.String(length=64), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column(
            "retention_expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("requested_by", sa.String(length=255), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "workflow IN ('summary', 'question')",
            name="ck_document_ai_analysis_runs_workflow",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'awaiting_primary_approval', "
            "'primary_running', 'awaiting_reviewer', "
            "'awaiting_reviewer_approval', 'reviewer_running', "
            "'completed', 'rejected', 'failed')",
            name="ck_document_ai_analysis_runs_status",
        ),
        sa.CheckConstraint(
            "content_state IN ('active', 'purged')",
            name="ck_document_ai_analysis_runs_content_state",
        ),
        sa.CheckConstraint(
            "retention_days >= 1 AND retention_days <= 3650",
            name="ck_document_ai_analysis_runs_retention_days",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_document_ai_analysis_runs_attempt_count",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_document_ai_analysis_runs_idempotency",
        ),
        sa.UniqueConstraint(
            "primary_request_id",
            name="uq_document_ai_analysis_runs_primary_request",
        ),
        sa.UniqueConstraint(
            "reviewer_request_id",
            name="uq_document_ai_analysis_runs_reviewer_request",
        ),
    )
    _indexes(
        "document_ai_analysis_runs",
        (
            "workspace_id",
            "workflow",
            "status",
            "idempotency_key",
            "request_fingerprint",
            "selection_fingerprint",
            "context_sha256",
            "effective_classification",
            "primary_provider",
            "primary_model",
            "primary_request_id",
            "reviewer_provider",
            "reviewer_model",
            "reviewer_request_id",
            "content_state",
            "retention_expires_at",
            "requested_by",
            "created_at",
        ),
    )

    op.create_table(
        "document_ai_analysis_citations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("analysis_run_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("citation_id", sa.String(length=32), nullable=False),
        sa.Column("citation_ordinal", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column("source_run_id", sa.String(length=64), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("source_ordinal", sa.Integer(), nullable=False),
        sa.Column("classification", sa.String(length=32), nullable=False),
        sa.Column("source_text_sha256", sa.String(length=64), nullable=False),
        sa.Column("fragment_text_sha256", sa.String(length=64), nullable=False),
        sa.Column("fragment_index", sa.Integer(), nullable=False),
        sa.Column("fragment_count", sa.Integer(), nullable=False),
        sa.Column("character_start", sa.Integer(), nullable=False),
        sa.Column("character_end", sa.Integer(), nullable=False),
        sa.Column("estimated_tokens", sa.Integer(), nullable=False),
        sa.Column("locators_json", sa.JSON(), nullable=False),
        sa.Column("injection_finding_codes_json", sa.JSON(), nullable=False),
        sa.Column("used_in_output", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "citation_ordinal >= 1",
            name="ck_document_ai_analysis_citations_ordinal",
        ),
        sa.CheckConstraint(
            "source_ordinal >= 0",
            name="ck_document_ai_analysis_citations_source_ordinal",
        ),
        sa.CheckConstraint(
            "fragment_index >= 1 AND fragment_count >= fragment_index",
            name="ck_document_ai_analysis_citations_fragment",
        ),
        sa.CheckConstraint(
            "character_start >= 0 AND character_end >= character_start",
            name="ck_document_ai_analysis_citations_range",
        ),
        sa.CheckConstraint(
            "estimated_tokens >= 0",
            name="ck_document_ai_analysis_citations_tokens",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"],
            ["document_ai_analysis_runs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "analysis_run_id",
            "citation_id",
            name="uq_document_ai_analysis_citations_id",
        ),
        sa.UniqueConstraint(
            "analysis_run_id",
            "citation_ordinal",
            name="uq_document_ai_analysis_citations_ordinal",
        ),
    )
    _indexes(
        "document_ai_analysis_citations",
        (
            "analysis_run_id",
            "workspace_id",
            "citation_id",
            "document_id",
            "source_run_id",
            "source_kind",
            "source_id",
            "classification",
            "source_text_sha256",
            "fragment_text_sha256",
            "used_in_output",
        ),
    )


def downgrade() -> None:
    op.drop_table("document_ai_analysis_citations")
    op.drop_table("document_ai_analysis_runs")
