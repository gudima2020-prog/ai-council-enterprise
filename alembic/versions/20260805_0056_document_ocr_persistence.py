"""P3-001.4b OCR Persistence, Retention and API.

Revision ID: 20260805_0056
Revises: 20260805_0055
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260805_0056"
down_revision = "20260805_0055"
branch_labels = None
depends_on = None


def _indexes(table: str, columns: tuple[str, ...]) -> None:
    for column in columns:
        op.create_index(
            f"ix_{table}_{column}",
            table,
            [column],
        )


def upgrade() -> None:
    op.create_table(
        "document_ocr_runs",
        sa.Column("id", sa.String(length=64), nullable=False),
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
            "status",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "ocr_version",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "source_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "request_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "selection_mode",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "requested_pages_json",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "classification",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "retention_policy",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "retention_days",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "retention_expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "text_state",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "text_purged_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "render_dpi",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "page_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "selected_page_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "blank_page_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "blank_page_numbers_json",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "total_characters",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "ocr_text_sha256",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "engine",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "engine_version",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "renderer",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "renderer_version",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "runtime_image",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "runtime_image_id",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column(
            "warnings_json",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "error_code",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column(
            "error_message",
            sa.String(length=2000),
            nullable=True,
        ),
        sa.Column(
            "error_details_json",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "requested_by",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "failed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
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
            "status IN "
            "('pending', 'running', 'completed', 'failed')",
            name="ck_document_ocr_runs_status",
        ),
        sa.CheckConstraint(
            "selection_mode IN ('all', 'explicit')",
            name="ck_document_ocr_runs_selection_mode",
        ),
        sa.CheckConstraint(
            "text_state IN ('active', 'purged')",
            name="ck_document_ocr_runs_text_state",
        ),
        sa.CheckConstraint(
            "retention_days >= 1 AND retention_days <= 3650",
            name="ck_document_ocr_runs_retention_days",
        ),
        sa.CheckConstraint(
            "render_dpi >= 1",
            name="ck_document_ocr_runs_render_dpi",
        ),
        sa.CheckConstraint(
            "page_count >= 0",
            name="ck_document_ocr_runs_page_count",
        ),
        sa.CheckConstraint(
            "selected_page_count >= 0",
            name="ck_document_ocr_runs_selected_page_count",
        ),
        sa.CheckConstraint(
            "blank_page_count >= 0",
            name="ck_document_ocr_runs_blank_page_count",
        ),
        sa.CheckConstraint(
            "total_characters >= 0",
            name="ck_document_ocr_runs_total_characters",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_document_ocr_runs_attempt_count",
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
        sa.UniqueConstraint(
            "document_id",
            "source_sha256",
            "ocr_version",
            "request_fingerprint",
            name="uq_document_ocr_runs_exact",
        ),
    )
    _indexes(
        "document_ocr_runs",
        (
            "document_id",
            "workspace_id",
            "status",
            "ocr_version",
            "source_sha256",
            "request_fingerprint",
            "classification",
            "retention_expires_at",
            "text_state",
            "requested_by",
            "created_at",
        ),
    )

    op.create_table(
        "document_ocr_pages",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column(
            "run_id",
            sa.String(length=64),
            nullable=False,
        ),
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
            "page_number",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "text_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "character_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "classification",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "retention_expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "width_pixels",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "height_pixels",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "pixel_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "png_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "png_size_bytes",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "warnings_json",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "page_number >= 1",
            name="ck_document_ocr_pages_page_number",
        ),
        sa.CheckConstraint(
            "character_count >= 0",
            name="ck_document_ocr_pages_character_count",
        ),
        sa.CheckConstraint(
            "width_pixels >= 1 AND height_pixels >= 1",
            name="ck_document_ocr_pages_dimensions",
        ),
        sa.CheckConstraint(
            "pixel_count >= 1",
            name="ck_document_ocr_pages_pixel_count",
        ),
        sa.CheckConstraint(
            "png_size_bytes >= 1",
            name="ck_document_ocr_pages_png_size_bytes",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["document_ocr_runs.id"],
            ondelete="CASCADE",
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
        sa.UniqueConstraint(
            "run_id",
            "page_number",
            name="uq_document_ocr_pages_page_number",
        ),
    )
    _indexes(
        "document_ocr_pages",
        (
            "run_id",
            "document_id",
            "workspace_id",
            "text_sha256",
            "classification",
            "retention_expires_at",
            "png_sha256",
        ),
    )


def downgrade() -> None:
    op.drop_table("document_ocr_pages")
    op.drop_table("document_ocr_runs")
