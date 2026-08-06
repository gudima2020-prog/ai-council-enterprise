from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.documents.ai_analysis import DocumentAIAnalysisError
from backend.documents.ai_context import (
    DocumentCitationLocator,
    DocumentContextSelection,
    DocumentContextSource,
    DocumentContextSourceKind,
    DocumentContextSourceRef,
)
from backend.documents.models import (
    DocumentExtractionChunkModel,
    DocumentExtractionRunModel,
    DocumentExtractionUnitModel,
    DocumentModel,
    DocumentOCRPageModel,
    DocumentOCRRunModel,
)
from backend.runtime_policy import DataClassification


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class DocumentAIContextResolver:
    """Resolves only exact, explicitly selected, persisted document sources."""

    MAX_CHUNK_LOCATORS = 256

    def __init__(self, *, session: Session) -> None:
        self._session = session

    def resolve(
        self,
        *,
        selection: DocumentContextSelection,
        now: datetime | None = None,
    ) -> tuple[DocumentContextSource, ...]:
        if not isinstance(selection, DocumentContextSelection):
            raise TypeError("selection must be a DocumentContextSelection")
        timestamp = _as_utc(now or utc_now())
        return tuple(
            self._resolve_reference(
                workspace_id=selection.workspace_id,
                reference=reference,
                now=timestamp,
            )
            for reference in selection.sources
        )

    def _resolve_reference(
        self,
        *,
        workspace_id: str,
        reference: DocumentContextSourceRef,
        now: datetime,
    ) -> DocumentContextSource:
        document = self._session.scalar(
            select(DocumentModel).where(
                DocumentModel.id == reference.document_id,
                DocumentModel.workspace_id == workspace_id,
                DocumentModel.status == "active",
            )
        )
        if document is None:
            raise self._not_found(reference)
        try:
            classification = DataClassification(document.classification)
        except ValueError as exc:
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_CLASSIFICATION_INVALID",
                "Selected document has an unsupported classification.",
                details={"document_id": reference.document_id},
            ) from exc

        if reference.source_kind is DocumentContextSourceKind.OCR_PAGE:
            return self._resolve_ocr_page(
                reference=reference,
                workspace_id=workspace_id,
                document=document,
                classification=classification,
                now=now,
            )
        return self._resolve_extraction(
            reference=reference,
            workspace_id=workspace_id,
            document=document,
            classification=classification,
        )

    def _resolve_extraction(
        self,
        *,
        reference: DocumentContextSourceRef,
        workspace_id: str,
        document: DocumentModel,
        classification: DataClassification,
    ) -> DocumentContextSource:
        run = self._session.scalar(
            select(DocumentExtractionRunModel).where(
                DocumentExtractionRunModel.id == reference.run_id,
                DocumentExtractionRunModel.document_id == reference.document_id,
                DocumentExtractionRunModel.workspace_id == workspace_id,
            )
        )
        if run is None:
            raise self._not_found(reference)
        if run.status != "completed":
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_EXTRACTION_UNAVAILABLE",
                "Selected extraction run is not completed.",
                details={
                    "document_id": reference.document_id,
                    "run_id": reference.run_id,
                    "status": run.status,
                },
            )
        if run.source_sha256 != document.content_sha256:
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_SOURCE_BINDING_MISMATCH",
                "Selected extraction run does not match the active document.",
                details={
                    "document_id": reference.document_id,
                    "run_id": reference.run_id,
                },
            )

        if reference.source_kind is DocumentContextSourceKind.EXTRACTION_UNIT:
            row = self._session.scalar(
                select(DocumentExtractionUnitModel).where(
                    DocumentExtractionUnitModel.id == reference.source_id,
                    DocumentExtractionUnitModel.run_id == reference.run_id,
                    DocumentExtractionUnitModel.document_id
                    == reference.document_id,
                    DocumentExtractionUnitModel.workspace_id == workspace_id,
                )
            )
            if row is None:
                raise self._not_found(reference)
            self._validate_text(
                reference=reference,
                text=row.text,
                persisted_sha256=row.text_sha256,
                character_count=row.character_count,
            )
            try:
                locator = DocumentCitationLocator.from_extraction_provenance(
                    unit_kind=row.kind,
                    unit_ordinal=row.ordinal,
                    provenance=dict(row.provenance_json or {}),
                )
            except (TypeError, ValueError) as exc:
                raise DocumentAIAnalysisError(
                    "DOCUMENT_AI_PROVENANCE_INVALID",
                    "Selected extraction unit provenance is invalid.",
                    details={
                        "document_id": reference.document_id,
                        "run_id": reference.run_id,
                        "source_id": reference.source_id,
                    },
                ) from exc
            return DocumentContextSource(
                workspace_id=workspace_id,
                document_id=reference.document_id,
                run_id=reference.run_id,
                source_kind=reference.source_kind,
                source_id=reference.source_id,
                classification=classification,
                ordinal=row.ordinal,
                text=row.text,
                text_sha256=row.text_sha256,
                locators=(locator,),
            )

        if reference.source_kind is not (
            DocumentContextSourceKind.EXTRACTION_CHUNK
        ):
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_SOURCE_KIND_INVALID",
                "Selected source kind is unsupported.",
                details={"source_kind": reference.source_kind.value},
            )
        chunk = self._session.scalar(
            select(DocumentExtractionChunkModel).where(
                DocumentExtractionChunkModel.id == reference.source_id,
                DocumentExtractionChunkModel.run_id == reference.run_id,
                DocumentExtractionChunkModel.document_id == reference.document_id,
                DocumentExtractionChunkModel.workspace_id == workspace_id,
            )
        )
        if chunk is None:
            raise self._not_found(reference)
        self._validate_text(
            reference=reference,
            text=chunk.text,
            persisted_sha256=chunk.text_sha256,
            character_count=chunk.character_count,
        )
        locators = self._chunk_locators(
            reference=reference,
            workspace_id=workspace_id,
            ordinals=chunk.source_unit_ordinals_json,
        )
        return DocumentContextSource(
            workspace_id=workspace_id,
            document_id=reference.document_id,
            run_id=reference.run_id,
            source_kind=reference.source_kind,
            source_id=reference.source_id,
            classification=classification,
            ordinal=chunk.ordinal,
            text=chunk.text,
            text_sha256=chunk.text_sha256,
            locators=locators,
        )

    def _chunk_locators(
        self,
        *,
        reference: DocumentContextSourceRef,
        workspace_id: str,
        ordinals: object,
    ) -> tuple[DocumentCitationLocator, ...]:
        if (
            not isinstance(ordinals, list)
            or not ordinals
            or len(ordinals) > self.MAX_CHUNK_LOCATORS
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for value in ordinals
            )
            or len(ordinals) != len(set(ordinals))
        ):
            raise self._provenance_error(reference)
        units = list(
            self._session.scalars(
                select(DocumentExtractionUnitModel)
                .where(
                    DocumentExtractionUnitModel.run_id == reference.run_id,
                    DocumentExtractionUnitModel.document_id
                    == reference.document_id,
                    DocumentExtractionUnitModel.workspace_id == workspace_id,
                    DocumentExtractionUnitModel.ordinal.in_(ordinals),
                )
                .order_by(DocumentExtractionUnitModel.ordinal.asc())
            ).all()
        )
        by_ordinal = {unit.ordinal: unit for unit in units}
        if set(by_ordinal) != set(ordinals):
            raise self._provenance_error(reference)
        try:
            return tuple(
                DocumentCitationLocator.from_extraction_provenance(
                    unit_kind=by_ordinal[ordinal].kind,
                    unit_ordinal=ordinal,
                    provenance=dict(
                        by_ordinal[ordinal].provenance_json or {}
                    ),
                )
                for ordinal in ordinals
            )
        except (TypeError, ValueError) as exc:
            raise self._provenance_error(reference) from exc

    def _resolve_ocr_page(
        self,
        *,
        reference: DocumentContextSourceRef,
        workspace_id: str,
        document: DocumentModel,
        classification: DataClassification,
        now: datetime,
    ) -> DocumentContextSource:
        run = self._session.scalar(
            select(DocumentOCRRunModel).where(
                DocumentOCRRunModel.id == reference.run_id,
                DocumentOCRRunModel.document_id == reference.document_id,
                DocumentOCRRunModel.workspace_id == workspace_id,
            )
        )
        if run is None:
            raise self._not_found(reference)
        if (
            run.status != "completed"
            or run.text_state != "active"
            or _as_utc(run.retention_expires_at) <= now
        ):
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_OCR_TEXT_UNAVAILABLE",
                "Selected OCR text is not active and unexpired.",
                details={
                    "document_id": reference.document_id,
                    "run_id": reference.run_id,
                    "status": run.status,
                    "text_state": run.text_state,
                },
            )
        if (
            run.source_sha256 != document.content_sha256
            or run.classification != classification.value
        ):
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_SOURCE_BINDING_MISMATCH",
                "Selected OCR run does not match the active document.",
                details={
                    "document_id": reference.document_id,
                    "run_id": reference.run_id,
                },
            )
        page = self._session.scalar(
            select(DocumentOCRPageModel).where(
                DocumentOCRPageModel.id == reference.source_id,
                DocumentOCRPageModel.run_id == reference.run_id,
                DocumentOCRPageModel.document_id == reference.document_id,
                DocumentOCRPageModel.workspace_id == workspace_id,
            )
        )
        if page is None:
            raise self._not_found(reference)
        if (
            page.classification != classification.value
            or _as_utc(page.retention_expires_at) <= now
        ):
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_OCR_TEXT_UNAVAILABLE",
                "Selected OCR page is not active and unexpired.",
                details={
                    "document_id": reference.document_id,
                    "run_id": reference.run_id,
                    "source_id": reference.source_id,
                },
            )
        self._validate_text(
            reference=reference,
            text=page.text,
            persisted_sha256=page.text_sha256,
            character_count=page.character_count,
        )
        return DocumentContextSource(
            workspace_id=workspace_id,
            document_id=reference.document_id,
            run_id=reference.run_id,
            source_kind=reference.source_kind,
            source_id=reference.source_id,
            classification=classification,
            ordinal=page.page_number - 1,
            text=page.text,
            text_sha256=page.text_sha256,
            locators=(
                DocumentCitationLocator.from_ocr_page(
                    page_number=page.page_number
                ),
            ),
        )

    @staticmethod
    def _validate_text(
        *,
        reference: DocumentContextSourceRef,
        text: str,
        persisted_sha256: str,
        character_count: int,
    ) -> None:
        calculated = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if (
            not text
            or "\x00" in text
            or calculated != persisted_sha256
            or len(text) != character_count
        ):
            raise DocumentAIAnalysisError(
                "DOCUMENT_AI_SOURCE_HASH_MISMATCH",
                "Selected persisted source failed integrity validation.",
                details={
                    "document_id": reference.document_id,
                    "run_id": reference.run_id,
                    "source_id": reference.source_id,
                },
            )

    @staticmethod
    def _not_found(
        reference: DocumentContextSourceRef,
    ) -> DocumentAIAnalysisError:
        return DocumentAIAnalysisError(
            "DOCUMENT_AI_SOURCE_NOT_FOUND",
            "Selected source was not found in this Workspace.",
            details={
                "document_id": reference.document_id,
                "run_id": reference.run_id,
                "source_kind": reference.source_kind.value,
                "source_id": reference.source_id,
            },
        )

    @staticmethod
    def _provenance_error(
        reference: DocumentContextSourceRef,
    ) -> DocumentAIAnalysisError:
        return DocumentAIAnalysisError(
            "DOCUMENT_AI_PROVENANCE_INVALID",
            "Selected extraction chunk provenance is invalid.",
            details={
                "document_id": reference.document_id,
                "run_id": reference.run_id,
                "source_id": reference.source_id,
            },
        )


__all__ = ["DocumentAIContextResolver"]
