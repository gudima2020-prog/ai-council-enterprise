from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.core.events import EventBus
from backend.database.base import Base
from backend.database.models import WorkspaceModel
from backend.documents.intake import (
    DocumentFormat,
    DocumentIntakeRequest,
)
from backend.documents.models import (
    DocumentOCRPageModel,
    DocumentOCRRunModel,
)
from backend.documents.ocr import (
    DocumentOCRError,
    DocumentOCRResult,
    OCRPageResult,
)
from backend.documents.ocr_service import (
    DocumentOCRConflictError,
    DocumentOCRService,
    DocumentOCRServiceError,
)
from backend.documents.service import DocumentRegistryService
from backend.documents.storage import ManagedDocumentStorage
from backend.runtime_policy import DataClassification

PDF_BYTES = b"%PDF-1.7\nocr persistence test\n%%EOF"
NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
IMAGE_ID = "sha256:" + ("a" * 64)


def add_workspace(session: Session, workspace_id: str) -> None:
    session.add(
        WorkspaceModel(
            id=workspace_id,
            name=workspace_id,
            description="",
            workspace_type="documents",
            status="active",
            metadata_json={},
        )
    )
    session.flush()


def make_page(
    page_number: int,
    *,
    text: str,
) -> OCRPageResult:
    return OCRPageResult(
        page_number=page_number,
        text=text,
        text_sha256=hashlib.sha256(
            text.encode("utf-8")
        ).hexdigest(),
        character_count=len(text),
        width_pixels=100,
        height_pixels=200,
        pixel_count=20_000,
        png_sha256=(f"{page_number:x}"[-1] * 64),
        png_size_bytes=500,
        engine="tesseract",
        engine_version="tesseract 5.5.0",
        renderer="pdfium",
        renderer_version="5.8.0",
        runtime_image="ai-studio-ocr-tesseract:5-v1",
        runtime_image_id=IMAGE_ID,
        warnings=("OCR_PAGE_NO_TEXT",) if not text else (),
    )


class SuccessfulOCR:
    OCR_VERSION = "p3-001.4a-v1"

    def __init__(self) -> None:
        self.calls: list[tuple[int, ...] | None] = []

    def recognize_pdf(
        self,
        *,
        descriptor,
        content: bytes,
        page_numbers: tuple[int, ...] | None,
    ) -> DocumentOCRResult:
        assert content == PDF_BYTES
        selected = page_numbers or (1, 2, 3)
        self.calls.append(page_numbers)
        pages = tuple(
            make_page(
                page_number,
                text=(
                    "" if page_number == 2 else f"Page {page_number}"
                ),
            )
            for page_number in selected
        )
        canonical_text = "\n\n".join(page.text for page in pages)
        warnings = tuple(
            f"OCR_PAGE_{page.page_number}_NO_TEXT"
            for page in pages
            if not page.text
        )
        return DocumentOCRResult(
            document_format=DocumentFormat.PDF,
            ocr_version=self.OCR_VERSION,
            source_sha256=descriptor.content_sha256,
            classification=descriptor.classification.value,
            render_dpi=200,
            page_count=3,
            selected_page_count=len(pages),
            total_characters=sum(
                page.character_count for page in pages
            ),
            ocr_text_sha256=hashlib.sha256(
                canonical_text.encode("utf-8")
            ).hexdigest(),
            pages=pages,
            warnings=warnings,
        )


class FailingOCR:
    OCR_VERSION = "p3-001.4a-v1"

    def recognize_pdf(self, **kwargs):
        raise DocumentOCRError(
            "DOCUMENT_OCR_ENGINE_FAILED",
            "secret OCR output must not be persisted",
            details={
                "page_number": 2,
                "text": "secret OCR output",
            },
        )


@pytest.fixture
def ocr_context(tmp_path: Path):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    add_workspace(session, "workspace_alpha")
    add_workspace(session, "workspace_beta")
    event_bus = EventBus()
    registry = DocumentRegistryService(
        session=session,
        event_bus=event_bus,
        storage=ManagedDocumentStorage(
            tmp_path / "documents"
        ),
    )
    ocr = SuccessfulOCR()
    service = DocumentOCRService(
        session=session,
        event_bus=event_bus,
        registry=registry,
        ocr=ocr,
    )

    yield session, registry, service, event_bus, ocr

    session.close()
    engine.dispose()


async def upload_pdf(
    registry: DocumentRegistryService,
    *,
    workspace_id: str = "workspace_alpha",
):
    return await registry.upload(
        request=DocumentIntakeRequest(
            workspace_id=workspace_id,
            filename="scan.pdf",
            content_type="application/pdf",
            classification=(
                DataClassification.CONFIDENTIAL
            ),
        ),
        content=PDF_BYTES,
        actor_id="operator_alpha",
    )


@pytest.mark.asyncio
async def test_ocr_persists_scoped_run_pages_and_retention(
    ocr_context,
) -> None:
    session, registry, service, _, _ = ocr_context
    uploaded = await upload_pdf(registry)

    result = await service.recognize(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        page_numbers=[3, 2, 2, 1],
        actor_id="operator_alpha",
        now=NOW,
    )

    assert result.created is True
    assert result.reused is False
    assert result.record.status == "completed"
    assert result.record.selection_mode == "explicit"
    assert result.record.requested_pages == (1, 2, 3)
    assert result.record.classification == "confidential"
    assert result.record.retention_days == 90
    assert result.record.retention_expires_at == (
        NOW + timedelta(days=90)
    )
    assert result.record.blank_page_numbers == (2,)
    assert result.record.to_public_dict()[
        "blank_page_recommendation"
    ] == "review_or_retry_blank_pages"

    pages = service.list_pages(
        run_id=result.record.id,
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        now=NOW,
    )
    assert [page.page_number for page in pages] == [1, 2, 3]
    assert all(page.classification == "confidential" for page in pages)
    assert session.scalar(
        select(func.count(DocumentOCRPageModel.id))
    ) == 3


@pytest.mark.asyncio
async def test_completed_exact_ocr_is_reused(
    ocr_context,
) -> None:
    _, registry, service, _, ocr = ocr_context
    uploaded = await upload_pdf(registry)
    first = await service.recognize(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        page_numbers=[2, 1],
        now=NOW,
    )
    second = await service.recognize(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        page_numbers=[1, 2],
        now=NOW + timedelta(minutes=1),
    )

    assert second.created is False
    assert second.reused is True
    assert second.record.id == first.record.id
    assert second.record.attempt_count == 1
    assert ocr.calls == [(1, 2)]


@pytest.mark.asyncio
async def test_failed_ocr_retries_in_place_with_safe_evidence(
    ocr_context,
) -> None:
    session, registry, service, event_bus, _ = ocr_context
    uploaded = await upload_pdf(registry)
    failing = DocumentOCRService(
        session=session,
        event_bus=event_bus,
        registry=registry,
        ocr=FailingOCR(),
    )
    failed = await failing.recognize(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        page_numbers=[2],
        now=NOW,
    )

    assert failed.record.status == "failed"
    assert failed.record.error_code == (
        "DOCUMENT_OCR_ENGINE_FAILED"
    )
    assert failed.record.error_details == {"page_number": 2}
    serialized = str(failed.record.to_public_dict())
    assert "secret OCR output" not in serialized

    completed = await service.recognize(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        page_numbers=[2],
        now=NOW + timedelta(minutes=1),
    )
    assert completed.record.id == failed.record.id
    assert completed.record.status == "completed"
    assert completed.record.attempt_count == 2
    assert completed.record.error_code is None


@pytest.mark.asyncio
async def test_running_exact_ocr_conflicts(
    ocr_context,
) -> None:
    session, registry, service, _, _ = ocr_context
    uploaded = await upload_pdf(registry)
    first = await service.recognize(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        page_numbers=[1],
        now=NOW,
    )
    row = session.get(DocumentOCRRunModel, first.record.id)
    assert row is not None
    row.status = "running"
    session.flush()

    with pytest.raises(DocumentOCRConflictError):
        await service.recognize(
            document_id=uploaded.record.id,
            workspace_id="workspace_alpha",
            page_numbers=[1],
            now=NOW,
        )


@pytest.mark.asyncio
async def test_workspace_isolation_hides_ocr_run(
    ocr_context,
) -> None:
    _, registry, service, _, _ = ocr_context
    uploaded = await upload_pdf(registry)
    result = await service.recognize(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        now=NOW,
    )

    with pytest.raises(Exception) as captured:
        service.get(
            run_id=result.record.id,
            document_id=uploaded.record.id,
            workspace_id="workspace_beta",
            now=NOW,
        )
    assert "Workspace" in str(captured.value)


@pytest.mark.asyncio
async def test_retention_purge_removes_text_but_keeps_evidence(
    ocr_context,
) -> None:
    session, registry, service, _, _ = ocr_context
    uploaded = await upload_pdf(registry)
    completed = await service.recognize(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        retention_days=1,
        now=NOW,
    )

    purged = await service.purge_expired(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        actor_id="retention_admin",
        now=NOW + timedelta(days=2),
    )
    assert purged.purged_runs == 1
    assert purged.purged_pages == 3
    record = service.get(
        run_id=completed.record.id,
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        now=NOW + timedelta(days=2),
    )
    assert record.text_state == "purged"
    assert record.text_purged_at == NOW + timedelta(days=2)
    assert record.ocr_text_sha256 == completed.record.ocr_text_sha256
    assert service.list_pages(
        run_id=completed.record.id,
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        now=NOW + timedelta(days=2),
    ) == []
    assert session.scalar(
        select(func.count(DocumentOCRPageModel.id))
    ) == 0


@pytest.mark.asyncio
async def test_purged_exact_ocr_can_be_reprocessed(
    ocr_context,
) -> None:
    _, registry, service, _, ocr = ocr_context
    uploaded = await upload_pdf(registry)
    first = await service.recognize(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        retention_days=1,
        now=NOW,
    )
    await service.purge_expired(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        now=NOW + timedelta(days=2),
    )
    retried = await service.recognize(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        retention_days=1,
        now=NOW + timedelta(days=2),
    )

    assert retried.record.id == first.record.id
    assert retried.record.attempt_count == 2
    assert retried.record.text_state == "active"
    assert len(ocr.calls) == 2


@pytest.mark.asyncio
async def test_document_delete_removes_all_ocr_content(
    ocr_context,
) -> None:
    session, registry, service, _, _ = ocr_context
    uploaded = await upload_pdf(registry)
    await service.recognize(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        page_numbers=[1, 2],
        now=NOW,
    )

    deleted = await registry.delete(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        actor_id="operator_alpha",
        now=NOW,
    )
    assert deleted.derived_deleted["ocr_runs"] == 1
    assert deleted.derived_deleted["ocr_pages"] == 2
    assert session.scalar(
        select(func.count(DocumentOCRRunModel.id))
    ) == 0
    assert session.scalar(
        select(func.count(DocumentOCRPageModel.id))
    ) == 0


@pytest.mark.asyncio
async def test_retention_cannot_exceed_classification_limit(
    ocr_context,
) -> None:
    session, registry, service, _, _ = ocr_context
    uploaded = await upload_pdf(registry)

    with pytest.raises(DocumentOCRServiceError) as captured:
        await service.recognize(
            document_id=uploaded.record.id,
            workspace_id="workspace_alpha",
            retention_days=91,
            now=NOW,
        )
    assert captured.value.code == "DOCUMENT_OCR_RETENTION_LIMIT"
    assert session.scalar(
        select(func.count(DocumentOCRRunModel.id))
    ) == 0


@pytest.mark.asyncio
async def test_ocr_events_are_metadata_only(
    ocr_context,
) -> None:
    _, registry, service, event_bus, _ = ocr_context
    uploaded = await upload_pdf(registry)
    seen = []

    async def capture(event):
        seen.append(event)

    event_bus.subscribe("document.ocr.*", capture)
    await service.recognize(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        now=NOW,
    )

    assert [event.event_type for event in seen] == [
        "document.ocr.completed"
    ]
    payload = str(seen[0].payload)
    assert "Page 1" not in payload
    assert "ocr persistence test" not in payload
    assert "text" not in seen[0].payload
