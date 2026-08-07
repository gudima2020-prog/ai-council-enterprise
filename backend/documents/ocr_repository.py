from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.documents.models import (
    DocumentOCRPageModel,
    DocumentOCRRunModel,
)
from backend.documents.ocr import DocumentOCRResult


class DocumentOCRRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_exact(
        self,
        *,
        document_id: str,
        workspace_id: str,
        source_sha256: str,
        ocr_version: str,
        request_fingerprint: str,
    ) -> DocumentOCRRunModel | None:
        return self._session.scalar(
            select(DocumentOCRRunModel).where(
                DocumentOCRRunModel.document_id
                == document_id,
                DocumentOCRRunModel.workspace_id
                == workspace_id,
                DocumentOCRRunModel.source_sha256
                == source_sha256,
                DocumentOCRRunModel.ocr_version
                == ocr_version,
                DocumentOCRRunModel.request_fingerprint
                == request_fingerprint,
            )
        )

    def create_pending(
        self,
        *,
        document_id: str,
        workspace_id: str,
        ocr_version: str,
        source_sha256: str,
        request_fingerprint: str,
        requested_pages: tuple[int, ...] | None,
        classification: str,
        retention_policy: str,
        retention_days: int,
        retention_expires_at: datetime,
        render_dpi: int,
        requested_by: str | None,
        now: datetime,
    ) -> DocumentOCRRunModel:
        row = DocumentOCRRunModel(
            document_id=document_id,
            workspace_id=workspace_id,
            status="pending",
            ocr_version=ocr_version,
            source_sha256=source_sha256,
            request_fingerprint=request_fingerprint,
            selection_mode=(
                "all" if requested_pages is None else "explicit"
            ),
            requested_pages_json=list(requested_pages or ()),
            classification=classification,
            retention_policy=retention_policy,
            retention_days=retention_days,
            retention_expires_at=retention_expires_at,
            text_state="active",
            text_purged_at=None,
            render_dpi=render_dpi,
            page_count=0,
            selected_page_count=0,
            blank_page_count=0,
            blank_page_numbers_json=[],
            total_characters=0,
            ocr_text_sha256=None,
            engine=None,
            engine_version=None,
            renderer=None,
            renderer_version=None,
            runtime_image=None,
            runtime_image_id=None,
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
        row: DocumentOCRRunModel,
        *,
        requested_by: str | None,
        retention_expires_at: datetime,
        now: datetime,
    ) -> DocumentOCRRunModel:
        self._delete_pages(row.id)
        row.status = "running"
        row.requested_by = requested_by
        row.attempt_count += 1
        row.retention_expires_at = retention_expires_at
        row.text_state = "active"
        row.text_purged_at = None
        row.page_count = 0
        row.selected_page_count = 0
        row.blank_page_count = 0
        row.blank_page_numbers_json = []
        row.total_characters = 0
        row.ocr_text_sha256 = None
        row.engine = None
        row.engine_version = None
        row.renderer = None
        row.renderer_version = None
        row.runtime_image = None
        row.runtime_image_id = None
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
        row: DocumentOCRRunModel,
        *,
        result: DocumentOCRResult,
        now: datetime,
    ) -> DocumentOCRRunModel:
        self._delete_pages(row.id)
        blank_page_numbers: list[int] = []

        for page in result.pages:
            if not page.text:
                blank_page_numbers.append(page.page_number)
            self._session.add(
                DocumentOCRPageModel(
                    run_id=row.id,
                    document_id=row.document_id,
                    workspace_id=row.workspace_id,
                    page_number=page.page_number,
                    text=page.text,
                    text_sha256=page.text_sha256,
                    character_count=page.character_count,
                    classification=row.classification,
                    retention_expires_at=(
                        row.retention_expires_at
                    ),
                    width_pixels=page.width_pixels,
                    height_pixels=page.height_pixels,
                    pixel_count=page.pixel_count,
                    png_sha256=page.png_sha256,
                    png_size_bytes=page.png_size_bytes,
                    warnings_json=list(page.warnings),
                    created_at=now,
                )
            )

        first_page = result.pages[0]
        row.status = "completed"
        row.ocr_version = result.ocr_version
        row.source_sha256 = result.source_sha256
        row.classification = result.classification
        row.render_dpi = result.render_dpi
        row.page_count = result.page_count
        row.selected_page_count = result.selected_page_count
        row.blank_page_count = len(blank_page_numbers)
        row.blank_page_numbers_json = blank_page_numbers
        row.total_characters = result.total_characters
        row.ocr_text_sha256 = result.ocr_text_sha256
        row.engine = first_page.engine
        row.engine_version = first_page.engine_version
        row.renderer = first_page.renderer
        row.renderer_version = first_page.renderer_version
        row.runtime_image = first_page.runtime_image
        row.runtime_image_id = first_page.runtime_image_id
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
        row: DocumentOCRRunModel,
        *,
        error_code: str,
        error_message: str,
        error_details: dict[str, Any],
        now: datetime,
    ) -> DocumentOCRRunModel:
        self._delete_pages(row.id)
        row.status = "failed"
        row.page_count = 0
        row.selected_page_count = 0
        row.blank_page_count = 0
        row.blank_page_numbers_json = []
        row.total_characters = 0
        row.ocr_text_sha256 = None
        row.engine = None
        row.engine_version = None
        row.renderer = None
        row.renderer_version = None
        row.runtime_image = None
        row.runtime_image_id = None
        row.warnings_json = []
        row.error_code = error_code
        row.error_message = error_message
        row.error_details_json = dict(error_details)
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
    ) -> DocumentOCRRunModel | None:
        return self._session.scalar(
            select(DocumentOCRRunModel).where(
                DocumentOCRRunModel.id == run_id,
                DocumentOCRRunModel.document_id == document_id,
                DocumentOCRRunModel.workspace_id == workspace_id,
            )
        )

    def list_runs(
        self,
        *,
        document_id: str,
        workspace_id: str,
        limit: int,
        offset: int,
    ) -> list[DocumentOCRRunModel]:
        return list(
            self._session.scalars(
                select(DocumentOCRRunModel)
                .where(
                    DocumentOCRRunModel.document_id
                    == document_id,
                    DocumentOCRRunModel.workspace_id
                    == workspace_id,
                )
                .order_by(
                    DocumentOCRRunModel.created_at.desc(),
                    DocumentOCRRunModel.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            ).all()
        )

    def list_pages(
        self,
        *,
        run_id: str,
        document_id: str,
        workspace_id: str,
        limit: int,
        offset: int,
    ) -> list[DocumentOCRPageModel]:
        return list(
            self._session.scalars(
                select(DocumentOCRPageModel)
                .where(
                    DocumentOCRPageModel.run_id == run_id,
                    DocumentOCRPageModel.document_id
                    == document_id,
                    DocumentOCRPageModel.workspace_id
                    == workspace_id,
                )
                .order_by(
                    DocumentOCRPageModel.page_number.asc()
                )
                .offset(offset)
                .limit(limit)
            ).all()
        )

    def purge_expired(
        self,
        *,
        workspace_id: str,
        now: datetime,
        document_id: str | None = None,
    ) -> dict[str, Any]:
        query = select(DocumentOCRRunModel).where(
            DocumentOCRRunModel.workspace_id == workspace_id,
            DocumentOCRRunModel.status == "completed",
            DocumentOCRRunModel.text_state == "active",
            DocumentOCRRunModel.retention_expires_at <= now,
        )
        if document_id is not None:
            query = query.where(
                DocumentOCRRunModel.document_id == document_id
            )
        rows = list(self._session.scalars(query).all())
        if not rows:
            return {"runs": 0, "pages": 0, "run_ids": []}

        run_ids = [row.id for row in rows]
        pages = self._session.execute(
            delete(DocumentOCRPageModel).where(
                DocumentOCRPageModel.run_id.in_(run_ids)
            )
        ).rowcount or 0
        for row in rows:
            row.text_state = "purged"
            row.text_purged_at = now
            row.updated_at = now
        self._session.flush()
        return {
            "runs": len(rows),
            "pages": int(pages),
            "run_ids": run_ids,
        }

    def delete_derived(
        self,
        *,
        document_id: str,
        workspace_id: str,
    ) -> dict[str, int]:
        run_ids = list(
            self._session.scalars(
                select(DocumentOCRRunModel.id).where(
                    DocumentOCRRunModel.document_id
                    == document_id,
                    DocumentOCRRunModel.workspace_id
                    == workspace_id,
                )
            ).all()
        )
        if not run_ids:
            return {"ocr_runs": 0, "ocr_pages": 0}

        pages = self._session.execute(
            delete(DocumentOCRPageModel).where(
                DocumentOCRPageModel.run_id.in_(run_ids)
            )
        ).rowcount or 0
        runs = self._session.execute(
            delete(DocumentOCRRunModel).where(
                DocumentOCRRunModel.id.in_(run_ids)
            )
        ).rowcount or 0
        self._session.flush()
        return {
            "ocr_runs": int(runs),
            "ocr_pages": int(pages),
        }

    def _delete_pages(self, run_id: str) -> None:
        self._session.execute(
            delete(DocumentOCRPageModel).where(
                DocumentOCRPageModel.run_id == run_id
            )
        )
