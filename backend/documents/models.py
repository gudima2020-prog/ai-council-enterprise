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
