from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class DocumentModel(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'deleted')",
            name="ck_documents_status",
        ),
        CheckConstraint(
            "storage_state IN ('ready', 'released', 'missing')",
            name="ck_documents_storage_state",
        ),
        UniqueConstraint(
            "workspace_id",
            "intake_fingerprint",
            name="uq_documents_workspace_intake_fingerprint",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("document"),
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    original_filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    safe_filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    document_format: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        index=True,
    )
    declared_mime_type: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    detected_mime_type: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    content_encoding: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    size_bytes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    content_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    intake_fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    classification: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    storage_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    storage_state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="ready",
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="active",
        index=True,
    )
    uploaded_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    deleted_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class DocumentEventModel(Base):
    __tablename__ = "document_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('document.uploaded', 'document.deleted')",
            name="ck_document_events_type",
        ),
        UniqueConstraint(
            "event_hash",
            name="uq_document_events_event_hash",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("document_event"),
    )
    sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        unique=True,
        index=True,
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    actor_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    previous_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    event_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )


class DocumentExtractionRunModel(Base):
    __tablename__ = "document_extraction_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN "
            "('pending', 'running', 'completed', 'failed')",
            name="ck_document_extraction_runs_status",
        ),
        CheckConstraint(
            "total_characters >= 0",
            name=(
                "ck_document_extraction_runs_"
                "total_characters"
            ),
        ),
        CheckConstraint(
            "unit_count >= 0",
            name=(
                "ck_document_extraction_runs_unit_count"
            ),
        ),
        CheckConstraint(
            "chunk_count >= 0",
            name=(
                "ck_document_extraction_runs_chunk_count"
            ),
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name=(
                "ck_document_extraction_runs_attempt_count"
            ),
        ),
        UniqueConstraint(
            "document_id",
            "source_sha256",
            "parser_version",
            name="uq_document_extraction_runs_exact",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("document_extraction"),
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        index=True,
    )
    parser: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    parser_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    source_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    extracted_text_sha256: Mapped[str | None] = (
        mapped_column(
            String(64),
            nullable=True,
        )
    )
    total_characters: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    unit_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    chunk_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    warnings_json: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    error_code: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(
        String(2000),
        nullable=True,
    )
    error_details_json: Mapped[dict[str, Any]] = (
        mapped_column(
            JSON,
            nullable=False,
            default=dict,
        )
    )
    requested_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class DocumentExtractionUnitModel(Base):
    __tablename__ = "document_extraction_units"
    __table_args__ = (
        CheckConstraint(
            "ordinal >= 0",
            name="ck_document_extraction_units_ordinal",
        ),
        CheckConstraint(
            "character_count >= 0",
            name=(
                "ck_document_extraction_units_"
                "character_count"
            ),
        ),
        UniqueConstraint(
            "run_id",
            "ordinal",
            name="uq_document_extraction_units_ordinal",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id(
            "document_extraction_unit"
        ),
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey(
            "document_extraction_runs.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    text_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    character_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    provenance_json: Mapped[dict[str, Any]] = (
        mapped_column(
            JSON,
            nullable=False,
            default=dict,
        )
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )


class DocumentExtractionChunkModel(Base):
    __tablename__ = "document_extraction_chunks"
    __table_args__ = (
        CheckConstraint(
            "ordinal >= 0",
            name="ck_document_extraction_chunks_ordinal",
        ),
        CheckConstraint(
            "character_count >= 0",
            name=(
                "ck_document_extraction_chunks_"
                "character_count"
            ),
        ),
        CheckConstraint(
            "character_start >= 0",
            name=(
                "ck_document_extraction_chunks_"
                "character_start"
            ),
        ),
        CheckConstraint(
            "character_end >= character_start",
            name=(
                "ck_document_extraction_chunks_range"
            ),
        ),
        UniqueConstraint(
            "run_id",
            "ordinal",
            name="uq_document_extraction_chunks_ordinal",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id(
            "document_extraction_chunk"
        ),
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey(
            "document_extraction_runs.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    text_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    character_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    character_start: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    character_end: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    source_unit_ordinals_json: Mapped[list[int]] = (
        mapped_column(
            JSON,
            nullable=False,
            default=list,
        )
    )
    provenance_json: Mapped[dict[str, Any]] = (
        mapped_column(
            JSON,
            nullable=False,
            default=dict,
        )
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
