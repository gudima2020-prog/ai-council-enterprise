"""P3-001.2a Document Registry and Managed Storage.

Revision ID: 20260804_0054
Revises: 20260731_0053
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260804_0054"
down_revision = "20260731_0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column(
            "workspace_id",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "original_filename",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "safe_filename",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "document_format",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "declared_mime_type",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "detected_mime_type",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "content_encoding",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "content_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "intake_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "classification",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "storage_key",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "storage_state",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "uploaded_by",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "deleted_by",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
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
        sa.CheckConstraint(
            "status IN ('active', 'deleted')",
            name="ck_documents_status",
        ),
        sa.CheckConstraint(
            "storage_state IN ('ready', 'released', 'missing')",
            name="ck_documents_storage_state",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "intake_fingerprint",
            name="uq_documents_workspace_intake_fingerprint",
        ),
    )
    for name in (
        "workspace_id",
        "safe_filename",
        "document_format",
        "content_sha256",
        "intake_fingerprint",
        "classification",
        "storage_key",
        "storage_state",
        "status",
        "created_at",
    ):
        op.create_index(
            f"ix_documents_{name}",
            "documents",
            [name],
        )

    op.create_table(
        "document_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column(
            "document_id",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "event_type",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "actor_id",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "previous_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "event_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('document.uploaded', 'document.deleted')",
            name="ck_document_events_type",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
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
            name="uq_document_events_event_hash",
        ),
    )
    for name in (
        "sequence",
        "document_id",
        "workspace_id",
        "event_type",
        "actor_id",
        "occurred_at",
        "event_hash",
    ):
        op.create_index(
            f"ix_document_events_{name}",
            "document_events",
            [name],
        )


def downgrade() -> None:
    op.drop_table("document_events")
    op.drop_table("documents")
