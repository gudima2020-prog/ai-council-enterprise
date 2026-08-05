from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from backend.core.events import Event, EventBus
from backend.database.models import WorkspaceModel
from backend.documents.intake import (
    DocumentIntakeDescriptor,
    DocumentIntakeRequest,
    DocumentIntakeService,
)
from backend.documents.models import (
    DocumentEventModel,
    DocumentModel,
)
from backend.documents.repository import DocumentRepository
from backend.documents.storage import (
    DocumentStorageError,
    ManagedDocumentStorage,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BLOCKED_METADATA_KEYS = {
    "bytes",
    "content",
    "file_bytes",
    "prompt",
    "raw_content",
    "response",
    "secret",
    "text",
    "token",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _assert_safe_metadata(
    value: Any,
    path: str = "metadata",
) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).casefold()
            if normalized in _BLOCKED_METADATA_KEYS:
                raise ValueError(
                    f"Unsafe document metadata key: {path}.{key}"
                )
            _assert_safe_metadata(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_safe_metadata(child, f"{path}[{index}]")


class DocumentRegistryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DocumentNotFoundError(DocumentRegistryError):
    pass


class DocumentWorkspaceError(DocumentRegistryError):
    pass


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    id: str
    workspace_id: str
    original_filename: str
    safe_filename: str
    document_format: str
    declared_mime_type: str
    detected_mime_type: str
    content_encoding: str | None
    size_bytes: int
    content_sha256: str
    intake_fingerprint: str
    classification: str
    storage_key: str
    storage_state: str
    status: str
    uploaded_by: str | None
    deleted_by: str | None
    deleted_at: datetime | None
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "original_filename": self.original_filename,
            "safe_filename": self.safe_filename,
            "document_format": self.document_format,
            "declared_mime_type": self.declared_mime_type,
            "detected_mime_type": self.detected_mime_type,
            "content_encoding": self.content_encoding,
            "size_bytes": self.size_bytes,
            "content_sha256": self.content_sha256,
            "intake_fingerprint": self.intake_fingerprint,
            "classification": self.classification,
            "storage_state": self.storage_state,
            "status": self.status,
            "uploaded_by": self.uploaded_by,
            "deleted_by": self.deleted_by,
            "deleted_at": self.deleted_at,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class DocumentUploadResult:
    record: DocumentRecord
    created: bool
    restored: bool
    storage_created: bool


@dataclass(frozen=True, slots=True)
class DocumentDeleteResult:
    record: DocumentRecord
    deleted: bool
    storage_deleted: bool


class DocumentRegistryService:
    def __init__(
        self,
        *,
        session: Session,
        event_bus: EventBus,
        storage: ManagedDocumentStorage | None = None,
        intake: DocumentIntakeService | None = None,
    ) -> None:
        self._session = session
        self._event_bus = event_bus
        self._storage = (
            storage
            or ManagedDocumentStorage.from_environment(
                PROJECT_ROOT
            )
        )
        self._intake = intake or DocumentIntakeService()
        self._repository = DocumentRepository(session)

    async def upload(
        self,
        *,
        request: DocumentIntakeRequest,
        content: bytes,
        actor_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> DocumentUploadResult:
        timestamp = now or utc_now()
        self._require_workspace(request.workspace_id)
        safe_metadata = dict(metadata or {})
        _assert_safe_metadata(safe_metadata)

        descriptor = self._intake.inspect_bytes(
            request,
            content,
        )
        existing = (
            self._repository.find_by_intake_fingerprint(
                workspace_id=descriptor.workspace_id,
                intake_fingerprint=(
                    descriptor.intake_fingerprint
                ),
            )
        )

        stored = self._storage.put(
            content=content,
            expected_sha256=descriptor.content_sha256,
            extension=descriptor.document_format.value,
        )

        if existing is not None and existing.status == "active":
            if existing.storage_key != stored.storage_key:
                raise DocumentRegistryError(
                    "DOCUMENT_STORAGE_KEY_MISMATCH",
                    "Active document storage binding changed.",
                )
            if existing.storage_state != "ready":
                existing.storage_state = "ready"
                existing.updated_at = timestamp
                self._session.flush()
            return DocumentUploadResult(
                record=self._to_record(existing),
                created=False,
                restored=False,
                storage_created=stored.created,
            )

        if existing is not None:
            row = self._repository.restore(
                existing,
                original_filename=descriptor.original_filename,
                safe_filename=descriptor.safe_filename,
                declared_mime_type=(
                    descriptor.declared_mime_type
                ),
                detected_mime_type=(
                    descriptor.detected_mime_type
                ),
                content_encoding=descriptor.content_encoding,
                storage_key=stored.storage_key,
                uploaded_by=actor_id,
                metadata=safe_metadata,
                now=timestamp,
            )
            created = False
            restored = True
        else:
            row = self._repository.create(
                workspace_id=descriptor.workspace_id,
                original_filename=descriptor.original_filename,
                safe_filename=descriptor.safe_filename,
                document_format=(
                    descriptor.document_format.value
                ),
                declared_mime_type=(
                    descriptor.declared_mime_type
                ),
                detected_mime_type=(
                    descriptor.detected_mime_type
                ),
                content_encoding=descriptor.content_encoding,
                size_bytes=descriptor.size_bytes,
                content_sha256=descriptor.content_sha256,
                intake_fingerprint=(
                    descriptor.intake_fingerprint
                ),
                classification=(
                    descriptor.classification.value
                ),
                storage_key=stored.storage_key,
                uploaded_by=actor_id,
                metadata=safe_metadata,
                now=timestamp,
            )
            created = True
            restored = False

        event_payload = self._event_payload(
            descriptor=descriptor,
            storage_key=stored.storage_key,
            restored=restored,
        )
        self._repository.append_event(
            document=row,
            event_type="document.uploaded",
            actor_id=actor_id,
            payload=event_payload,
            occurred_at=timestamp,
        )
        await self._event_bus.publish(
            Event(
                event_type="document.uploaded",
                source="documents.registry",
                workspace_id=row.workspace_id,
                payload={
                    "document_id": row.id,
                    **event_payload,
                },
            )
        )
        return DocumentUploadResult(
            record=self._to_record(row),
            created=created,
            restored=restored,
            storage_created=stored.created,
        )

    def get(
        self,
        *,
        document_id: str,
        workspace_id: str,
        include_deleted: bool = False,
    ) -> DocumentRecord:
        row = self._repository.get(
            document_id=document_id,
            workspace_id=workspace_id,
            include_deleted=include_deleted,
        )
        if row is None:
            raise DocumentNotFoundError(
                "DOCUMENT_NOT_FOUND",
                "Document was not found in this Workspace.",
            )
        return self._to_record(row)

    def list(
        self,
        *,
        workspace_id: str,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DocumentRecord]:
        self._require_workspace(workspace_id)
        bounded_limit = max(1, min(limit, 500))
        bounded_offset = max(0, offset)
        return [
            self._to_record(row)
            for row in self._repository.list(
                workspace_id=workspace_id,
                include_deleted=include_deleted,
                limit=bounded_limit,
                offset=bounded_offset,
            )
        ]

    def read_content(
        self,
        *,
        document_id: str,
        workspace_id: str,
    ) -> bytes:
        row = self._repository.get(
            document_id=document_id,
            workspace_id=workspace_id,
            include_deleted=False,
        )
        if row is None:
            raise DocumentNotFoundError(
                "DOCUMENT_NOT_FOUND",
                "Document was not found in this Workspace.",
            )
        try:
            return self._storage.read_verified(
                storage_key=row.storage_key,
                expected_sha256=row.content_sha256,
                expected_size=row.size_bytes,
            )
        except DocumentStorageError:
            self._repository.mark_missing(
                row,
                now=utc_now(),
            )
            raise

    async def delete(
        self,
        *,
        document_id: str,
        workspace_id: str,
        actor_id: str | None = None,
        now: datetime | None = None,
    ) -> DocumentDeleteResult:
        timestamp = now or utc_now()
        row = self._repository.get(
            document_id=document_id,
            workspace_id=workspace_id,
            include_deleted=True,
        )
        if row is None:
            raise DocumentNotFoundError(
                "DOCUMENT_NOT_FOUND",
                "Document was not found in this Workspace.",
            )
        if row.status == "deleted":
            return DocumentDeleteResult(
                record=self._to_record(row),
                deleted=False,
                storage_deleted=False,
            )

        active_references = (
            self._repository.active_storage_references(
                storage_key=row.storage_key,
                excluding_document_id=row.id,
            )
        )
        storage_deleted = False
        if active_references == 0:
            storage_deleted = self._storage.delete(
                storage_key=row.storage_key,
                expected_sha256=row.content_sha256,
            )

        self._repository.mark_deleted(
            row,
            deleted_by=actor_id,
            now=timestamp,
        )
        event_payload = {
            "content_sha256": row.content_sha256,
            "document_format": row.document_format,
            "size_bytes": row.size_bytes,
            "classification": row.classification,
            "intake_fingerprint": row.intake_fingerprint,
            "storage_deleted": storage_deleted,
            "shared_storage_references": (
                active_references
            ),
        }
        self._repository.append_event(
            document=row,
            event_type="document.deleted",
            actor_id=actor_id,
            payload=event_payload,
            occurred_at=timestamp,
        )
        await self._event_bus.publish(
            Event(
                event_type="document.deleted",
                source="documents.registry",
                workspace_id=row.workspace_id,
                payload={
                    "document_id": row.id,
                    **event_payload,
                },
            )
        )
        return DocumentDeleteResult(
            record=self._to_record(row),
            deleted=True,
            storage_deleted=storage_deleted,
        )

    def events(
        self,
        *,
        document_id: str,
        workspace_id: str,
    ) -> list[dict[str, Any]]:
        self.get(
            document_id=document_id,
            workspace_id=workspace_id,
            include_deleted=True,
        )
        return [
            self._public_event(row)
            for row in self._repository.list_events(
                document_id=document_id,
                workspace_id=workspace_id,
            )
        ]

    def verify_event_chain(self) -> bool:
        return self._repository.verify_event_chain()

    def _require_workspace(
        self,
        workspace_id: str,
    ) -> WorkspaceModel:
        row = self._session.get(
            WorkspaceModel,
            workspace_id,
        )
        if row is None:
            raise DocumentWorkspaceError(
                "DOCUMENT_WORKSPACE_NOT_FOUND",
                "Workspace does not exist.",
            )
        return row

    @staticmethod
    def _event_payload(
        *,
        descriptor: DocumentIntakeDescriptor,
        storage_key: str,
        restored: bool,
    ) -> dict[str, Any]:
        return {
            "content_sha256": descriptor.content_sha256,
            "document_format": (
                descriptor.document_format.value
            ),
            "detected_mime_type": (
                descriptor.detected_mime_type
            ),
            "size_bytes": descriptor.size_bytes,
            "classification": (
                descriptor.classification.value
            ),
            "intake_fingerprint": (
                descriptor.intake_fingerprint
            ),
            "storage_key": storage_key,
            "restored": restored,
            "warnings": list(descriptor.warnings),
        }

    @staticmethod
    def _to_record(row: DocumentModel) -> DocumentRecord:
        return DocumentRecord(
            id=row.id,
            workspace_id=row.workspace_id,
            original_filename=row.original_filename,
            safe_filename=row.safe_filename,
            document_format=row.document_format,
            declared_mime_type=row.declared_mime_type,
            detected_mime_type=row.detected_mime_type,
            content_encoding=row.content_encoding,
            size_bytes=row.size_bytes,
            content_sha256=row.content_sha256,
            intake_fingerprint=row.intake_fingerprint,
            classification=row.classification,
            storage_key=row.storage_key,
            storage_state=row.storage_state,
            status=row.status,
            uploaded_by=row.uploaded_by,
            deleted_by=row.deleted_by,
            deleted_at=row.deleted_at,
            metadata=dict(row.metadata_json or {}),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _public_event(
        row: DocumentEventModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "sequence": row.sequence,
            "document_id": row.document_id,
            "workspace_id": row.workspace_id,
            "event_type": row.event_type,
            "actor_id": row.actor_id,
            "payload": dict(row.payload_json or {}),
            "occurred_at": row.occurred_at,
            "previous_hash": row.previous_hash,
            "event_hash": row.event_hash,
            "created_at": row.created_at,
        }
