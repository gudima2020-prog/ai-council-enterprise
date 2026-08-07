from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from anyio import to_thread
from sqlalchemy.orm import Session

from backend.core.events import Event, EventBus
from backend.documents.chunking import (
    DeterministicDocumentChunker,
    DocumentChunkingError,
)
from backend.documents.extraction import (
    DeterministicDocumentExtractor,
    DocumentExtractionError,
)
from backend.documents.extraction_repository import (
    DocumentExtractionRepository,
)
from backend.documents.intake import (
    DocumentFormat,
    DocumentIntakeDescriptor,
)
from backend.documents.models import (
    DocumentExtractionChunkModel,
    DocumentExtractionRunModel,
    DocumentExtractionUnitModel,
)
from backend.documents.service import (
    DocumentRecord,
    DocumentRegistryService,
)
from backend.documents.storage import DocumentStorageError
from backend.runtime_policy import DataClassification


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DocumentExtractionServiceError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code


class DocumentExtractionNotFoundError(
    DocumentExtractionServiceError
):
    pass


class DocumentExtractionConflictError(
    DocumentExtractionServiceError
):
    pass


@dataclass(frozen=True, slots=True)
class DocumentExtractionRunRecord:
    id: str
    document_id: str
    workspace_id: str
    status: str
    parser: str
    parser_version: str
    source_sha256: str
    extracted_text_sha256: str | None
    total_characters: int
    unit_count: int
    chunk_count: int
    warnings: tuple[str, ...]
    error_code: str | None
    error_message: str | None
    error_details: dict[str, Any]
    requested_by: str | None
    attempt_count: int
    started_at: datetime | None
    completed_at: datetime | None
    failed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "document_id": self.document_id,
            "workspace_id": self.workspace_id,
            "status": self.status,
            "parser": self.parser,
            "parser_version": self.parser_version,
            "source_sha256": self.source_sha256,
            "extracted_text_sha256": (
                self.extracted_text_sha256
            ),
            "total_characters": self.total_characters,
            "unit_count": self.unit_count,
            "chunk_count": self.chunk_count,
            "warnings": list(self.warnings),
            "error_code": self.error_code,
            "error_message": self.error_message,
            "error_details": dict(self.error_details),
            "requested_by": self.requested_by,
            "attempt_count": self.attempt_count,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "failed_at": self.failed_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class DocumentExtractionUnitRecord:
    id: str
    run_id: str
    document_id: str
    workspace_id: str
    ordinal: int
    kind: str
    text: str
    text_sha256: str
    character_count: int
    provenance: dict[str, Any]
    created_at: datetime

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "document_id": self.document_id,
            "workspace_id": self.workspace_id,
            "ordinal": self.ordinal,
            "kind": self.kind,
            "text": self.text,
            "text_sha256": self.text_sha256,
            "character_count": self.character_count,
            "provenance": dict(self.provenance),
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class DocumentExtractionChunkRecord:
    id: str
    run_id: str
    document_id: str
    workspace_id: str
    ordinal: int
    text: str
    text_sha256: str
    character_count: int
    character_start: int
    character_end: int
    source_unit_ordinals: tuple[int, ...]
    provenance: dict[str, Any]
    created_at: datetime

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "document_id": self.document_id,
            "workspace_id": self.workspace_id,
            "ordinal": self.ordinal,
            "text": self.text,
            "text_sha256": self.text_sha256,
            "character_count": self.character_count,
            "character_start": self.character_start,
            "character_end": self.character_end,
            "source_unit_ordinals": list(
                self.source_unit_ordinals
            ),
            "provenance": dict(self.provenance),
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class DocumentExtractionExecutionResult:
    record: DocumentExtractionRunRecord
    created: bool
    reused: bool


class DocumentExtractionService:
    def __init__(
        self,
        *,
        session: Session,
        event_bus: EventBus,
        registry: DocumentRegistryService | None = None,
        extractor: DeterministicDocumentExtractor | None = None,
        chunker: DeterministicDocumentChunker | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._registry = (
            registry
            or DocumentRegistryService(
                session=session,
                event_bus=event_bus,
            )
        )
        self._extractor = (
            extractor
            or DeterministicDocumentExtractor()
        )
        self._chunker = (
            chunker
            or DeterministicDocumentChunker()
        )
        self._repository = (
            DocumentExtractionRepository(session)
        )

    async def extract(
        self,
        *,
        document_id: str,
        workspace_id: str,
        actor_id: str | None = None,
        now: datetime | None = None,
    ) -> DocumentExtractionExecutionResult:
        timestamp = now or utc_now()
        document = self._registry.get(
            document_id=document_id,
            workspace_id=workspace_id,
            include_deleted=False,
        )
        parser_version = (
            self._extractor.PARSER_VERSION
        )
        parser = self._parser_name(
            document.document_format
        )

        row = self._repository.find_exact(
            document_id=document.id,
            workspace_id=document.workspace_id,
            source_sha256=document.content_sha256,
            parser_version=parser_version,
        )
        created = row is None

        if (
            row is not None
            and row.status == "completed"
        ):
            return DocumentExtractionExecutionResult(
                record=self._to_run_record(row),
                created=False,
                reused=True,
            )

        if (
            row is not None
            and row.status == "running"
        ):
            raise DocumentExtractionConflictError(
                "DOCUMENT_EXTRACTION_ALREADY_RUNNING",
                "Document extraction is already running.",
            )

        if row is None:
            row = self._repository.create_pending(
                document_id=document.id,
                workspace_id=document.workspace_id,
                parser=parser,
                parser_version=parser_version,
                source_sha256=document.content_sha256,
                requested_by=actor_id,
                now=timestamp,
            )

        self._repository.mark_running(
            row,
            requested_by=actor_id,
            now=timestamp,
        )

        try:
            content = self._registry.read_content(
                document_id=document.id,
                workspace_id=document.workspace_id,
            )
            descriptor = self._descriptor(document)
            result, chunks = await to_thread.run_sync(
                lambda: self._extract_and_chunk(
                    descriptor,
                    content,
                )
            )
        except (
            DocumentExtractionError,
            DocumentChunkingError,
            DocumentStorageError,
        ) as exc:
            error_code = getattr(
                exc,
                "code",
                "DOCUMENT_EXTRACTION_FAILED",
            )
            details = dict(
                getattr(exc, "details", {}) or {}
            )
            row = self._repository.fail(
                row,
                error_code=str(error_code),
                error_message=str(exc),
                error_details=details,
                now=timestamp,
            )
            await self._publish_failed(
                row,
                actor_id=actor_id,
            )
            return DocumentExtractionExecutionResult(
                record=self._to_run_record(row),
                created=created,
                reused=False,
            )
        except Exception:
            row = self._repository.fail(
                row,
                error_code=(
                    "DOCUMENT_EXTRACTION_INTERNAL_ERROR"
                ),
                error_message=(
                    "Document extraction failed unexpectedly."
                ),
                error_details={},
                now=timestamp,
            )
            await self._publish_failed(
                row,
                actor_id=actor_id,
            )
            return DocumentExtractionExecutionResult(
                record=self._to_run_record(row),
                created=created,
                reused=False,
            )

        row = self._repository.complete(
            row,
            result=result,
            chunks=chunks,
            now=timestamp,
        )
        await self._event_bus.publish(
            Event(
                event_type=(
                    "document.extraction.completed"
                ),
                source="documents.extraction",
                workspace_id=row.workspace_id,
                payload={
                    "document_id": row.document_id,
                    "extraction_id": row.id,
                    "parser": row.parser,
                    "parser_version": row.parser_version,
                    "source_sha256": row.source_sha256,
                    "extracted_text_sha256": (
                        row.extracted_text_sha256
                    ),
                    "unit_count": row.unit_count,
                    "chunk_count": row.chunk_count,
                    "total_characters": (
                        row.total_characters
                    ),
                    "warnings": list(
                        row.warnings_json or []
                    ),
                    "actor_id": actor_id,
                },
            )
        )
        return DocumentExtractionExecutionResult(
            record=self._to_run_record(row),
            created=created,
            reused=False,
        )

    def get(
        self,
        *,
        run_id: str,
        document_id: str,
        workspace_id: str,
    ) -> DocumentExtractionRunRecord:
        self._registry.get(
            document_id=document_id,
            workspace_id=workspace_id,
            include_deleted=False,
        )
        row = self._repository.get(
            run_id=run_id,
            document_id=document_id,
            workspace_id=workspace_id,
        )
        if row is None:
            raise DocumentExtractionNotFoundError(
                "DOCUMENT_EXTRACTION_NOT_FOUND",
                "Document extraction was not found "
                "in this Workspace.",
            )
        return self._to_run_record(row)

    def list_runs(
        self,
        *,
        document_id: str,
        workspace_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DocumentExtractionRunRecord]:
        self._registry.get(
            document_id=document_id,
            workspace_id=workspace_id,
            include_deleted=False,
        )
        bounded_limit = max(1, min(limit, 500))
        bounded_offset = max(0, offset)
        return [
            self._to_run_record(row)
            for row in self._repository.list_runs(
                document_id=document_id,
                workspace_id=workspace_id,
                limit=bounded_limit,
                offset=bounded_offset,
            )
        ]

    def list_units(
        self,
        *,
        run_id: str,
        document_id: str,
        workspace_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DocumentExtractionUnitRecord]:
        run = self.get(
            run_id=run_id,
            document_id=document_id,
            workspace_id=workspace_id,
        )
        if run.status != "completed":
            return []
        bounded_limit = max(1, min(limit, 500))
        bounded_offset = max(0, offset)
        return [
            self._to_unit_record(row)
            for row in self._repository.list_units(
                run_id=run_id,
                document_id=document_id,
                workspace_id=workspace_id,
                limit=bounded_limit,
                offset=bounded_offset,
            )
        ]

    def list_chunks(
        self,
        *,
        run_id: str,
        document_id: str,
        workspace_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DocumentExtractionChunkRecord]:
        run = self.get(
            run_id=run_id,
            document_id=document_id,
            workspace_id=workspace_id,
        )
        if run.status != "completed":
            return []
        bounded_limit = max(1, min(limit, 500))
        bounded_offset = max(0, offset)
        return [
            self._to_chunk_record(row)
            for row in self._repository.list_chunks(
                run_id=run_id,
                document_id=document_id,
                workspace_id=workspace_id,
                limit=bounded_limit,
                offset=bounded_offset,
            )
        ]

    def _extract_and_chunk(
        self,
        descriptor: DocumentIntakeDescriptor,
        content: bytes,
    ):
        result = self._extractor.extract(
            descriptor=descriptor,
            content=content,
        )
        return result, self._chunker.chunk(result)

    async def _publish_failed(
        self,
        row: DocumentExtractionRunModel,
        *,
        actor_id: str | None,
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type="document.extraction.failed",
                source="documents.extraction",
                workspace_id=row.workspace_id,
                payload={
                    "document_id": row.document_id,
                    "extraction_id": row.id,
                    "parser": row.parser,
                    "parser_version": row.parser_version,
                    "source_sha256": row.source_sha256,
                    "error_code": row.error_code,
                    "error_details": dict(
                        row.error_details_json or {}
                    ),
                    "actor_id": actor_id,
                },
            )
        )

    @staticmethod
    def _descriptor(
        document: DocumentRecord,
    ) -> DocumentIntakeDescriptor:
        return DocumentIntakeDescriptor(
            workspace_id=document.workspace_id,
            original_filename=(
                document.original_filename
            ),
            safe_filename=document.safe_filename,
            document_format=DocumentFormat(
                document.document_format
            ),
            declared_mime_type=(
                document.declared_mime_type
            ),
            detected_mime_type=(
                document.detected_mime_type
            ),
            content_encoding=(
                document.content_encoding
            ),
            size_bytes=document.size_bytes,
            content_sha256=document.content_sha256,
            intake_fingerprint=(
                document.intake_fingerprint
            ),
            classification=DataClassification(
                document.classification
            ),
            warnings=(),
        )

    @staticmethod
    def _parser_name(
        document_format: str,
    ) -> str:
        mapping = {
            "pdf": "pypdf",
            "docx": "python-docx",
            "xlsx": "openpyxl",
            "txt": "builtin-text",
        }
        try:
            return mapping[document_format]
        except KeyError as exc:
            raise DocumentExtractionServiceError(
                "DOCUMENT_EXTRACTION_FORMAT_UNSUPPORTED",
                "Document format is not supported "
                "for extraction.",
            ) from exc

    @staticmethod
    def _to_run_record(
        row: DocumentExtractionRunModel,
    ) -> DocumentExtractionRunRecord:
        return DocumentExtractionRunRecord(
            id=row.id,
            document_id=row.document_id,
            workspace_id=row.workspace_id,
            status=row.status,
            parser=row.parser,
            parser_version=row.parser_version,
            source_sha256=row.source_sha256,
            extracted_text_sha256=(
                row.extracted_text_sha256
            ),
            total_characters=row.total_characters,
            unit_count=row.unit_count,
            chunk_count=row.chunk_count,
            warnings=tuple(
                row.warnings_json or []
            ),
            error_code=row.error_code,
            error_message=row.error_message,
            error_details=dict(
                row.error_details_json or {}
            ),
            requested_by=row.requested_by,
            attempt_count=row.attempt_count,
            started_at=row.started_at,
            completed_at=row.completed_at,
            failed_at=row.failed_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _to_unit_record(
        row: DocumentExtractionUnitModel,
    ) -> DocumentExtractionUnitRecord:
        return DocumentExtractionUnitRecord(
            id=row.id,
            run_id=row.run_id,
            document_id=row.document_id,
            workspace_id=row.workspace_id,
            ordinal=row.ordinal,
            kind=row.kind,
            text=row.text,
            text_sha256=row.text_sha256,
            character_count=row.character_count,
            provenance=dict(
                row.provenance_json or {}
            ),
            created_at=row.created_at,
        )

    @staticmethod
    def _to_chunk_record(
        row: DocumentExtractionChunkModel,
    ) -> DocumentExtractionChunkRecord:
        return DocumentExtractionChunkRecord(
            id=row.id,
            run_id=row.run_id,
            document_id=row.document_id,
            workspace_id=row.workspace_id,
            ordinal=row.ordinal,
            text=row.text,
            text_sha256=row.text_sha256,
            character_count=row.character_count,
            character_start=row.character_start,
            character_end=row.character_end,
            source_unit_ordinals=tuple(
                row.source_unit_ordinals_json or []
            ),
            provenance=dict(
                row.provenance_json or {}
            ),
            created_at=row.created_at,
        )
