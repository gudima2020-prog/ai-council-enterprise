from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from anyio import to_thread
from sqlalchemy.orm import Session

from backend.core.events import Event, EventBus
from backend.documents.intake import (
    DocumentFormat,
    DocumentIntakeDescriptor,
)
from backend.documents.models import (
    DocumentOCRPageModel,
    DocumentOCRRunModel,
)
from backend.documents.ocr import (
    DocumentOCRError,
    DocumentOCRPolicy,
    IsolatedPDFOCR,
)
from backend.documents.ocr_repository import (
    DocumentOCRRepository,
)
from backend.documents.service import (
    DocumentRecord,
    DocumentRegistryService,
)
from backend.documents.storage import DocumentStorageError
from backend.runtime_policy import DataClassification


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


_SAFE_ERROR_DETAIL_KEYS = {
    "page_number",
    "page_count",
    "selected_page_count",
    "pixel_count",
    "total_pixels",
    "png_size_bytes",
    "characters",
    "timeout_seconds",
    "returncode",
}
_SAFE_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_RUNTIME_UNAVAILABLE_CODES = {
    "DOCUMENT_OCR_RUNTIME_IMAGE_MISSING",
    "DOCUMENT_OCR_RUNTIME_UNAVAILABLE",
    "DOCUMENT_OCR_RUNTIME_UNTRUSTED",
}


class DocumentOCRServiceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DocumentOCRNotFoundError(DocumentOCRServiceError):
    pass


class DocumentOCRConflictError(DocumentOCRServiceError):
    pass


@dataclass(frozen=True, slots=True)
class DocumentOCRRetentionPolicy:
    policy_version: str = "classification-retention-v1"
    public_days: int = 365
    internal_days: int = 180
    confidential_days: int = 90
    restricted_days: int = 30
    max_days: int = 3650

    def __post_init__(self) -> None:
        values = (
            self.public_days,
            self.internal_days,
            self.confidential_days,
            self.restricted_days,
        )
        if self.max_days < 1 or self.max_days > 3650:
            raise ValueError("max_days must be between 1 and 3650")
        if any(value < 1 or value > self.max_days for value in values):
            raise ValueError(
                "OCR retention defaults must be within policy bounds"
            )

    def resolve(
        self,
        *,
        classification: str,
        requested_days: int | None,
    ) -> int:
        try:
            maximum = {
                DataClassification.PUBLIC.value: self.public_days,
                DataClassification.INTERNAL.value: self.internal_days,
                DataClassification.CONFIDENTIAL.value: (
                    self.confidential_days
                ),
                DataClassification.RESTRICTED.value: (
                    self.restricted_days
                ),
            }[classification]
        except KeyError as exc:
            raise DocumentOCRServiceError(
                "DOCUMENT_OCR_CLASSIFICATION_INVALID",
                "Document classification is not supported for OCR.",
            ) from exc

        if requested_days is None:
            return maximum
        if isinstance(requested_days, bool) or not isinstance(
            requested_days,
            int,
        ):
            raise DocumentOCRServiceError(
                "DOCUMENT_OCR_RETENTION_INVALID",
                "OCR retention_days must be an integer.",
            )
        if requested_days < 1 or requested_days > maximum:
            raise DocumentOCRServiceError(
                "DOCUMENT_OCR_RETENTION_LIMIT",
                "OCR retention_days exceeds the classification policy.",
            )
        return requested_days


@dataclass(frozen=True, slots=True)
class DocumentOCRRunRecord:
    id: str
    document_id: str
    workspace_id: str
    status: str
    ocr_version: str
    source_sha256: str
    request_fingerprint: str
    selection_mode: str
    requested_pages: tuple[int, ...]
    classification: str
    retention_policy: str
    retention_days: int
    retention_expires_at: datetime
    text_state: str
    text_purged_at: datetime | None
    render_dpi: int
    page_count: int
    selected_page_count: int
    blank_page_count: int
    blank_page_numbers: tuple[int, ...]
    total_characters: int
    ocr_text_sha256: str | None
    engine: str | None
    engine_version: str | None
    renderer: str | None
    renderer_version: str | None
    runtime_image: str | None
    runtime_image_id: str | None
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
            "ocr_version": self.ocr_version,
            "source_sha256": self.source_sha256,
            "request_fingerprint": self.request_fingerprint,
            "selection_mode": self.selection_mode,
            "requested_pages": list(self.requested_pages),
            "classification": self.classification,
            "retention_policy": self.retention_policy,
            "retention_days": self.retention_days,
            "retention_expires_at": self.retention_expires_at,
            "text_state": self.text_state,
            "text_purged_at": self.text_purged_at,
            "render_dpi": self.render_dpi,
            "page_count": self.page_count,
            "selected_page_count": self.selected_page_count,
            "blank_page_count": self.blank_page_count,
            "blank_page_numbers": list(
                self.blank_page_numbers
            ),
            "blank_page_recommendation": (
                "review_or_retry_blank_pages"
                if self.blank_page_numbers
                else None
            ),
            "total_characters": self.total_characters,
            "ocr_text_sha256": self.ocr_text_sha256,
            "engine": self.engine,
            "engine_version": self.engine_version,
            "renderer": self.renderer,
            "renderer_version": self.renderer_version,
            "runtime_image": self.runtime_image,
            "runtime_image_id": self.runtime_image_id,
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
class DocumentOCRPageRecord:
    id: str
    run_id: str
    document_id: str
    workspace_id: str
    page_number: int
    text: str
    text_sha256: str
    character_count: int
    classification: str
    retention_expires_at: datetime
    width_pixels: int
    height_pixels: int
    pixel_count: int
    png_sha256: str
    png_size_bytes: int
    warnings: tuple[str, ...]
    created_at: datetime

    def to_public_dict(
        self,
        *,
        include_text: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "run_id": self.run_id,
            "document_id": self.document_id,
            "workspace_id": self.workspace_id,
            "page_number": self.page_number,
            "text_sha256": self.text_sha256,
            "character_count": self.character_count,
            "classification": self.classification,
            "retention_expires_at": (
                self.retention_expires_at
            ),
            "width_pixels": self.width_pixels,
            "height_pixels": self.height_pixels,
            "pixel_count": self.pixel_count,
            "png_sha256": self.png_sha256,
            "png_size_bytes": self.png_size_bytes,
            "warnings": list(self.warnings),
            "created_at": self.created_at,
        }
        if include_text:
            payload["text"] = self.text
        return payload


@dataclass(frozen=True, slots=True)
class DocumentOCRExecutionResult:
    record: DocumentOCRRunRecord
    created: bool
    reused: bool


@dataclass(frozen=True, slots=True)
class DocumentOCRPurgeResult:
    workspace_id: str
    document_id: str
    purged_runs: int
    purged_pages: int

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "document_id": self.document_id,
            "purged_runs": self.purged_runs,
            "purged_pages": self.purged_pages,
        }


class DocumentOCRService:
    def __init__(
        self,
        *,
        session: Session,
        event_bus: EventBus,
        registry: DocumentRegistryService | None = None,
        ocr: IsolatedPDFOCR | None = None,
        ocr_policy: DocumentOCRPolicy | None = None,
        retention_policy: DocumentOCRRetentionPolicy | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._registry = (
            registry
            or DocumentRegistryService(
                session=session,
                event_bus=event_bus,
            )
        )
        self._ocr_policy = ocr_policy or DocumentOCRPolicy()
        self._ocr = ocr or IsolatedPDFOCR(
            policy=self._ocr_policy
        )
        self._retention = (
            retention_policy or DocumentOCRRetentionPolicy()
        )
        self._repository = DocumentOCRRepository(session)

    async def recognize(
        self,
        *,
        document_id: str,
        workspace_id: str,
        page_numbers: Iterable[int] | None = None,
        retention_days: int | None = None,
        actor_id: str | None = None,
        now: datetime | None = None,
    ) -> DocumentOCRExecutionResult:
        timestamp = now or utc_now()
        document = self._registry.get(
            document_id=document_id,
            workspace_id=workspace_id,
            include_deleted=False,
        )
        requested_pages = self._normalize_pages(page_numbers)
        resolved_retention_days = self._retention.resolve(
            classification=document.classification,
            requested_days=retention_days,
        )
        retention_expires_at = timestamp + timedelta(
            days=resolved_retention_days
        )
        ocr_version = str(self._ocr.OCR_VERSION)
        request_fingerprint = self._request_fingerprint(
            ocr_version=ocr_version,
            requested_pages=requested_pages,
            retention_days=resolved_retention_days,
        )

        self._repository.purge_expired(
            workspace_id=workspace_id,
            document_id=document_id,
            now=timestamp,
        )
        row = self._repository.find_exact(
            document_id=document.id,
            workspace_id=document.workspace_id,
            source_sha256=document.content_sha256,
            ocr_version=ocr_version,
            request_fingerprint=request_fingerprint,
        )
        created = row is None

        if (
            row is not None
            and row.status == "completed"
            and row.text_state == "active"
        ):
            return DocumentOCRExecutionResult(
                record=self._to_run_record(row),
                created=False,
                reused=True,
            )
        if row is not None and row.status == "running":
            raise DocumentOCRConflictError(
                "DOCUMENT_OCR_ALREADY_RUNNING",
                "Document OCR is already running.",
            )
        if row is None:
            row = self._repository.create_pending(
                document_id=document.id,
                workspace_id=document.workspace_id,
                ocr_version=ocr_version,
                source_sha256=document.content_sha256,
                request_fingerprint=request_fingerprint,
                requested_pages=requested_pages,
                classification=document.classification,
                retention_policy=(
                    self._retention.policy_version
                ),
                retention_days=resolved_retention_days,
                retention_expires_at=retention_expires_at,
                render_dpi=self._ocr_policy.render_dpi,
                requested_by=actor_id,
                now=timestamp,
            )

        self._repository.mark_running(
            row,
            requested_by=actor_id,
            retention_expires_at=retention_expires_at,
            now=timestamp,
        )

        try:
            content = self._registry.read_content(
                document_id=document.id,
                workspace_id=document.workspace_id,
            )
            descriptor = self._descriptor(document)
            result = await to_thread.run_sync(
                lambda: self._ocr.recognize_pdf(
                    descriptor=descriptor,
                    content=content,
                    page_numbers=requested_pages,
                )
            )
        except (DocumentOCRError, DocumentStorageError) as exc:
            code = self._safe_error_code(
                getattr(exc, "code", "DOCUMENT_OCR_FAILED")
            )
            details = self._safe_error_details(
                getattr(exc, "details", {})
            )
            row = self._repository.fail(
                row,
                error_code=code,
                error_message=self._safe_error_message(code),
                error_details=details,
                now=timestamp,
            )
            await self._publish_failed(row, actor_id=actor_id)
            return DocumentOCRExecutionResult(
                record=self._to_run_record(row),
                created=created,
                reused=False,
            )
        except Exception:
            row = self._repository.fail(
                row,
                error_code="DOCUMENT_OCR_INTERNAL_ERROR",
                error_message=(
                    "Document OCR failed unexpectedly."
                ),
                error_details={},
                now=timestamp,
            )
            await self._publish_failed(row, actor_id=actor_id)
            return DocumentOCRExecutionResult(
                record=self._to_run_record(row),
                created=created,
                reused=False,
            )

        row = self._repository.complete(
            row,
            result=result,
            now=timestamp,
        )
        await self._event_bus.publish(
            Event(
                event_type="document.ocr.completed",
                source="documents.ocr",
                workspace_id=row.workspace_id,
                payload={
                    "document_id": row.document_id,
                    "ocr_run_id": row.id,
                    "ocr_version": row.ocr_version,
                    "source_sha256": row.source_sha256,
                    "request_fingerprint": (
                        row.request_fingerprint
                    ),
                    "classification": row.classification,
                    "retention_policy": row.retention_policy,
                    "retention_days": row.retention_days,
                    "retention_expires_at": (
                        row.retention_expires_at.isoformat()
                    ),
                    "page_count": row.page_count,
                    "selected_page_count": (
                        row.selected_page_count
                    ),
                    "blank_page_count": row.blank_page_count,
                    "total_characters": row.total_characters,
                    "ocr_text_sha256": row.ocr_text_sha256,
                    "engine": row.engine,
                    "engine_version": row.engine_version,
                    "renderer": row.renderer,
                    "renderer_version": row.renderer_version,
                    "runtime_image_id": row.runtime_image_id,
                    "warnings": list(row.warnings_json or []),
                    "actor_id": actor_id,
                },
            )
        )
        return DocumentOCRExecutionResult(
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
        now: datetime | None = None,
    ) -> DocumentOCRRunRecord:
        self._registry.get(
            document_id=document_id,
            workspace_id=workspace_id,
            include_deleted=False,
        )
        self._repository.purge_expired(
            workspace_id=workspace_id,
            document_id=document_id,
            now=now or utc_now(),
        )
        row = self._repository.get(
            run_id=run_id,
            document_id=document_id,
            workspace_id=workspace_id,
        )
        if row is None:
            raise DocumentOCRNotFoundError(
                "DOCUMENT_OCR_RUN_NOT_FOUND",
                "Document OCR run was not found in this Workspace.",
            )
        return self._to_run_record(row)

    def list_runs(
        self,
        *,
        document_id: str,
        workspace_id: str,
        limit: int = 100,
        offset: int = 0,
        now: datetime | None = None,
    ) -> list[DocumentOCRRunRecord]:
        self._registry.get(
            document_id=document_id,
            workspace_id=workspace_id,
            include_deleted=False,
        )
        self._repository.purge_expired(
            workspace_id=workspace_id,
            document_id=document_id,
            now=now or utc_now(),
        )
        return [
            self._to_run_record(row)
            for row in self._repository.list_runs(
                document_id=document_id,
                workspace_id=workspace_id,
                limit=max(1, min(limit, 500)),
                offset=max(0, offset),
            )
        ]

    def list_pages(
        self,
        *,
        run_id: str,
        document_id: str,
        workspace_id: str,
        limit: int = 100,
        offset: int = 0,
        now: datetime | None = None,
    ) -> list[DocumentOCRPageRecord]:
        run = self.get(
            run_id=run_id,
            document_id=document_id,
            workspace_id=workspace_id,
            now=now,
        )
        if run.status != "completed" or run.text_state != "active":
            return []
        return [
            self._to_page_record(row)
            for row in self._repository.list_pages(
                run_id=run_id,
                document_id=document_id,
                workspace_id=workspace_id,
                limit=max(1, min(limit, 100)),
                offset=max(0, offset),
            )
        ]

    async def purge_expired(
        self,
        *,
        document_id: str,
        workspace_id: str,
        actor_id: str | None = None,
        now: datetime | None = None,
    ) -> DocumentOCRPurgeResult:
        timestamp = now or utc_now()
        self._registry.get(
            document_id=document_id,
            workspace_id=workspace_id,
            include_deleted=False,
        )
        result = self._repository.purge_expired(
            workspace_id=workspace_id,
            document_id=document_id,
            now=timestamp,
        )
        if result["runs"]:
            await self._event_bus.publish(
                Event(
                    event_type=(
                        "document.ocr.retention_purged"
                    ),
                    source="documents.ocr",
                    workspace_id=workspace_id,
                    payload={
                        "document_id": document_id,
                        "purged_runs": result["runs"],
                        "purged_pages": result["pages"],
                        "purged_at": timestamp.isoformat(),
                        "actor_id": actor_id,
                    },
                )
            )
        return DocumentOCRPurgeResult(
            workspace_id=workspace_id,
            document_id=document_id,
            purged_runs=int(result["runs"]),
            purged_pages=int(result["pages"]),
        )

    async def _publish_failed(
        self,
        row: DocumentOCRRunModel,
        *,
        actor_id: str | None,
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type="document.ocr.failed",
                source="documents.ocr",
                workspace_id=row.workspace_id,
                payload={
                    "document_id": row.document_id,
                    "ocr_run_id": row.id,
                    "ocr_version": row.ocr_version,
                    "source_sha256": row.source_sha256,
                    "request_fingerprint": (
                        row.request_fingerprint
                    ),
                    "classification": row.classification,
                    "retention_policy": row.retention_policy,
                    "retention_days": row.retention_days,
                    "error_code": row.error_code,
                    "error_details": dict(
                        row.error_details_json or {}
                    ),
                    "actor_id": actor_id,
                },
            )
        )

    def _normalize_pages(
        self,
        values: Iterable[int] | None,
    ) -> tuple[int, ...] | None:
        if values is None:
            return None
        selected: set[int] = set()
        try:
            for value in values:
                if isinstance(value, bool):
                    raise ValueError
                page_number = int(value)
                if page_number != value:
                    raise ValueError
                selected.add(page_number)
        except (TypeError, ValueError) as exc:
            raise DocumentOCRServiceError(
                "DOCUMENT_OCR_PAGE_SELECTION_INVALID",
                "OCR page selection is invalid.",
            ) from exc
        ordered = tuple(sorted(selected))
        if not ordered:
            raise DocumentOCRServiceError(
                "DOCUMENT_OCR_PAGE_SELECTION_EMPTY",
                "At least one OCR page must be selected.",
            )
        if len(ordered) > self._ocr_policy.max_selected_pages:
            raise DocumentOCRServiceError(
                "DOCUMENT_OCR_SELECTED_PAGE_LIMIT",
                "Selected OCR page count exceeds the configured limit.",
            )
        if ordered[0] < 1:
            raise DocumentOCRServiceError(
                "DOCUMENT_OCR_PAGE_OUT_OF_RANGE",
                "OCR page selection must use 1-based positive pages.",
            )
        return ordered

    def _request_fingerprint(
        self,
        *,
        ocr_version: str,
        requested_pages: tuple[int, ...] | None,
        retention_days: int,
    ) -> str:
        payload = {
            "ocr_version": ocr_version,
            "page_selection": (
                "all"
                if requested_pages is None
                else list(requested_pages)
            ),
            "ocr_policy": asdict(self._ocr_policy),
            "retention_policy": self._retention.policy_version,
            "retention_days": retention_days,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _descriptor(
        document: DocumentRecord,
    ) -> DocumentIntakeDescriptor:
        return DocumentIntakeDescriptor(
            workspace_id=document.workspace_id,
            original_filename=document.original_filename,
            safe_filename=document.safe_filename,
            document_format=DocumentFormat(
                document.document_format
            ),
            declared_mime_type=document.declared_mime_type,
            detected_mime_type=document.detected_mime_type,
            content_encoding=document.content_encoding,
            size_bytes=document.size_bytes,
            content_sha256=document.content_sha256,
            intake_fingerprint=document.intake_fingerprint,
            classification=DataClassification(
                document.classification
            ),
            warnings=(),
        )

    @staticmethod
    def _safe_error_code(value: Any) -> str:
        normalized = str(value)
        if not _SAFE_ERROR_CODE_RE.fullmatch(normalized):
            return "DOCUMENT_OCR_FAILED"
        return normalized

    @staticmethod
    def _safe_error_details(value: Any) -> dict[str, int]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, int] = {}
        for key, child in value.items():
            if (
                str(key) in _SAFE_ERROR_DETAIL_KEYS
                and type(child) is int
                and -(2**63) <= child < 2**63
            ):
                result[str(key)] = child
        return result

    @staticmethod
    def _safe_error_message(code: str) -> str:
        if code in _RUNTIME_UNAVAILABLE_CODES:
            return "Trusted OCR runtime is unavailable."
        if code.startswith("DOCUMENT_STORAGE_"):
            return "Managed document storage is unavailable for OCR."
        return "Document OCR processing failed."

    @staticmethod
    def _to_run_record(
        row: DocumentOCRRunModel,
    ) -> DocumentOCRRunRecord:
        return DocumentOCRRunRecord(
            id=row.id,
            document_id=row.document_id,
            workspace_id=row.workspace_id,
            status=row.status,
            ocr_version=row.ocr_version,
            source_sha256=row.source_sha256,
            request_fingerprint=row.request_fingerprint,
            selection_mode=row.selection_mode,
            requested_pages=tuple(
                row.requested_pages_json or []
            ),
            classification=row.classification,
            retention_policy=row.retention_policy,
            retention_days=row.retention_days,
            retention_expires_at=(
                as_utc(row.retention_expires_at)
                or row.retention_expires_at
            ),
            text_state=row.text_state,
            text_purged_at=as_utc(row.text_purged_at),
            render_dpi=row.render_dpi,
            page_count=row.page_count,
            selected_page_count=row.selected_page_count,
            blank_page_count=row.blank_page_count,
            blank_page_numbers=tuple(
                row.blank_page_numbers_json or []
            ),
            total_characters=row.total_characters,
            ocr_text_sha256=row.ocr_text_sha256,
            engine=row.engine,
            engine_version=row.engine_version,
            renderer=row.renderer,
            renderer_version=row.renderer_version,
            runtime_image=row.runtime_image,
            runtime_image_id=row.runtime_image_id,
            warnings=tuple(row.warnings_json or []),
            error_code=row.error_code,
            error_message=row.error_message,
            error_details=dict(
                row.error_details_json or {}
            ),
            requested_by=row.requested_by,
            attempt_count=row.attempt_count,
            started_at=as_utc(row.started_at),
            completed_at=as_utc(row.completed_at),
            failed_at=as_utc(row.failed_at),
            created_at=as_utc(row.created_at) or row.created_at,
            updated_at=as_utc(row.updated_at) or row.updated_at,
        )

    @staticmethod
    def _to_page_record(
        row: DocumentOCRPageModel,
    ) -> DocumentOCRPageRecord:
        return DocumentOCRPageRecord(
            id=row.id,
            run_id=row.run_id,
            document_id=row.document_id,
            workspace_id=row.workspace_id,
            page_number=row.page_number,
            text=row.text,
            text_sha256=row.text_sha256,
            character_count=row.character_count,
            classification=row.classification,
            retention_expires_at=(
                as_utc(row.retention_expires_at)
                or row.retention_expires_at
            ),
            width_pixels=row.width_pixels,
            height_pixels=row.height_pixels,
            pixel_count=row.pixel_count,
            png_sha256=row.png_sha256,
            png_size_bytes=row.png_size_bytes,
            warnings=tuple(row.warnings_json or []),
            created_at=as_utc(row.created_at) or row.created_at,
        )
