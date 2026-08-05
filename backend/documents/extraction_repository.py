from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.documents.chunking import DocumentTextChunk
from backend.documents.extraction import (
    DocumentExtractionResult,
)
from backend.documents.models import (
    DocumentExtractionChunkModel,
    DocumentExtractionRunModel,
    DocumentExtractionUnitModel,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DocumentExtractionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_exact(
        self,
        *,
        document_id: str,
        workspace_id: str,
        source_sha256: str,
        parser_version: str,
    ) -> DocumentExtractionRunModel | None:
        return self._session.scalar(
            select(DocumentExtractionRunModel).where(
                DocumentExtractionRunModel.document_id
                == document_id,
                DocumentExtractionRunModel.workspace_id
                == workspace_id,
                DocumentExtractionRunModel.source_sha256
                == source_sha256,
                DocumentExtractionRunModel.parser_version
                == parser_version,
            )
        )

    def create_pending(
        self,
        *,
        document_id: str,
        workspace_id: str,
        parser: str,
        parser_version: str,
        source_sha256: str,
        requested_by: str | None,
        now: datetime,
    ) -> DocumentExtractionRunModel:
        row = DocumentExtractionRunModel(
            document_id=document_id,
            workspace_id=workspace_id,
            status="pending",
            parser=parser,
            parser_version=parser_version,
            source_sha256=source_sha256,
            extracted_text_sha256=None,
            total_characters=0,
            unit_count=0,
            chunk_count=0,
            warnings_json=[],
            error_code=None,
            error_message=None,
            error_details_json={},
            requested_by=requested_by,
            attempt_count=0,
            started_at=None,
            completed_at=None,
            failed_at=None,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def mark_running(
        self,
        row: DocumentExtractionRunModel,
        *,
        requested_by: str | None,
        now: datetime,
    ) -> DocumentExtractionRunModel:
        self._delete_materialized(row.id)
        row.status = "running"
        row.requested_by = requested_by
        row.attempt_count += 1
        row.extracted_text_sha256 = None
        row.total_characters = 0
        row.unit_count = 0
        row.chunk_count = 0
        row.warnings_json = []
        row.error_code = None
        row.error_message = None
        row.error_details_json = {}
        row.started_at = now
        row.completed_at = None
        row.failed_at = None
        row.updated_at = now
        self._session.flush()
        return row

    def complete(
        self,
        row: DocumentExtractionRunModel,
        *,
        result: DocumentExtractionResult,
        chunks: tuple[DocumentTextChunk, ...],
        now: datetime,
    ) -> DocumentExtractionRunModel:
        self._delete_materialized(row.id)

        for unit in result.units:
            self._session.add(
                DocumentExtractionUnitModel(
                    run_id=row.id,
                    document_id=row.document_id,
                    workspace_id=row.workspace_id,
                    ordinal=unit.ordinal,
                    kind=unit.kind.value,
                    text=unit.text,
                    text_sha256=unit.text_sha256,
                    character_count=len(unit.text),
                    provenance_json=dict(
                        unit.provenance
                    ),
                    created_at=now,
                )
            )

        for chunk in chunks:
            self._session.add(
                DocumentExtractionChunkModel(
                    run_id=row.id,
                    document_id=row.document_id,
                    workspace_id=row.workspace_id,
                    ordinal=chunk.ordinal,
                    text=chunk.text,
                    text_sha256=chunk.text_sha256,
                    character_count=len(chunk.text),
                    character_start=(
                        chunk.character_start
                    ),
                    character_end=chunk.character_end,
                    source_unit_ordinals_json=list(
                        chunk.source_unit_ordinals
                    ),
                    provenance_json=dict(
                        chunk.provenance
                    ),
                    created_at=now,
                )
            )

        row.status = "completed"
        row.parser = result.parser
        row.parser_version = result.parser_version
        row.source_sha256 = result.source_sha256
        row.extracted_text_sha256 = (
            result.extracted_text_sha256
        )
        row.total_characters = (
            result.total_characters
        )
        row.unit_count = result.unit_count
        row.chunk_count = len(chunks)
        row.warnings_json = list(result.warnings)
        row.error_code = None
        row.error_message = None
        row.error_details_json = {}
        row.completed_at = now
        row.failed_at = None
        row.updated_at = now
        self._session.flush()
        return row

    def fail(
        self,
        row: DocumentExtractionRunModel,
        *,
        error_code: str,
        error_message: str,
        error_details: dict[str, Any],
        now: datetime,
    ) -> DocumentExtractionRunModel:
        self._delete_materialized(row.id)
        row.status = "failed"
        row.extracted_text_sha256 = None
        row.total_characters = 0
        row.unit_count = 0
        row.chunk_count = 0
        row.warnings_json = []
        row.error_code = error_code
        row.error_message = error_message
        row.error_details_json = dict(
            error_details
        )
        row.completed_at = None
        row.failed_at = now
        row.updated_at = now
        self._session.flush()
        return row

    def get(
        self,
        *,
        run_id: str,
        document_id: str,
        workspace_id: str,
    ) -> DocumentExtractionRunModel | None:
        return self._session.scalar(
            select(DocumentExtractionRunModel).where(
                DocumentExtractionRunModel.id == run_id,
                DocumentExtractionRunModel.document_id
                == document_id,
                DocumentExtractionRunModel.workspace_id
                == workspace_id,
            )
        )

    def list_runs(
        self,
        *,
        document_id: str,
        workspace_id: str,
        limit: int,
        offset: int,
    ) -> list[DocumentExtractionRunModel]:
        return list(
            self._session.scalars(
                select(DocumentExtractionRunModel)
                .where(
                    DocumentExtractionRunModel.document_id
                    == document_id,
                    DocumentExtractionRunModel.workspace_id
                    == workspace_id,
                )
                .order_by(
                    DocumentExtractionRunModel.created_at.desc(),
                    DocumentExtractionRunModel.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            ).all()
        )

    def list_units(
        self,
        *,
        run_id: str,
        document_id: str,
        workspace_id: str,
        limit: int,
        offset: int,
    ) -> list[DocumentExtractionUnitModel]:
        return list(
            self._session.scalars(
                select(DocumentExtractionUnitModel)
                .where(
                    DocumentExtractionUnitModel.run_id
                    == run_id,
                    DocumentExtractionUnitModel.document_id
                    == document_id,
                    DocumentExtractionUnitModel.workspace_id
                    == workspace_id,
                )
                .order_by(
                    DocumentExtractionUnitModel.ordinal.asc()
                )
                .offset(offset)
                .limit(limit)
            ).all()
        )

    def list_chunks(
        self,
        *,
        run_id: str,
        document_id: str,
        workspace_id: str,
        limit: int,
        offset: int,
    ) -> list[DocumentExtractionChunkModel]:
        return list(
            self._session.scalars(
                select(DocumentExtractionChunkModel)
                .where(
                    DocumentExtractionChunkModel.run_id
                    == run_id,
                    DocumentExtractionChunkModel.document_id
                    == document_id,
                    DocumentExtractionChunkModel.workspace_id
                    == workspace_id,
                )
                .order_by(
                    DocumentExtractionChunkModel.ordinal.asc()
                )
                .offset(offset)
                .limit(limit)
            ).all()
        )

    def delete_derived(
        self,
        *,
        document_id: str,
        workspace_id: str,
    ) -> dict[str, int]:
        run_ids = list(
            self._session.scalars(
                select(DocumentExtractionRunModel.id).where(
                    DocumentExtractionRunModel.document_id
                    == document_id,
                    DocumentExtractionRunModel.workspace_id
                    == workspace_id,
                )
            ).all()
        )
        if not run_ids:
            return {
                "runs": 0,
                "units": 0,
                "chunks": 0,
            }

        chunks = self._session.execute(
            delete(DocumentExtractionChunkModel).where(
                DocumentExtractionChunkModel.run_id.in_(
                    run_ids
                )
            )
        ).rowcount or 0
        units = self._session.execute(
            delete(DocumentExtractionUnitModel).where(
                DocumentExtractionUnitModel.run_id.in_(
                    run_ids
                )
            )
        ).rowcount or 0
        runs = self._session.execute(
            delete(DocumentExtractionRunModel).where(
                DocumentExtractionRunModel.id.in_(
                    run_ids
                )
            )
        ).rowcount or 0
        self._session.flush()
        return {
            "runs": int(runs),
            "units": int(units),
            "chunks": int(chunks),
        }

    def _delete_materialized(
        self,
        run_id: str,
    ) -> None:
        self._session.execute(
            delete(DocumentExtractionChunkModel).where(
                DocumentExtractionChunkModel.run_id
                == run_id
            )
        )
        self._session.execute(
            delete(DocumentExtractionUnitModel).where(
                DocumentExtractionUnitModel.run_id
                == run_id
            )
        )
