from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.core.events import EventBus
from backend.database.base import Base
from backend.database.models import WorkspaceModel
from backend.documents.ai_analysis import DocumentAIAnalysisError
from backend.documents.ai_context import (
    DocumentCitationLocationKind,
    DocumentContextSelection,
    DocumentContextSourceKind,
    DocumentContextSourceRef,
)
from backend.documents.ai_resolver import DocumentAIContextResolver
from backend.documents.extraction_service import DocumentExtractionService
from backend.documents.intake import DocumentIntakeRequest
from backend.documents.models import (
    DocumentExtractionUnitModel,
    DocumentOCRRunModel,
)
from backend.documents.ocr import DocumentOCRResult, OCRPageResult
from backend.documents.ocr_service import DocumentOCRService
from backend.documents.service import DocumentRegistryService
from backend.documents.storage import ManagedDocumentStorage
from backend.runtime_policy import DataClassification

NOW = datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc)
TXT_BYTES = b"Council evidence for resolver."
PDF_BYTES = b"%PDF-1.7\nresolver OCR\n%%EOF"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class OnePageOCR:
    OCR_VERSION = "test-ocr-v1"

    def recognize_pdf(self, *, descriptor, content, page_numbers):
        assert content == PDF_BYTES
        assert page_numbers is None
        text = "OCR evidence on page one."
        page = OCRPageResult(
            page_number=1,
            text=text,
            text_sha256=_sha256(text),
            character_count=len(text),
            width_pixels=100,
            height_pixels=100,
            pixel_count=10_000,
            png_sha256="a" * 64,
            png_size_bytes=200,
            engine="test",
            engine_version="1",
            renderer="test",
            renderer_version="1",
            runtime_image="test:1",
            runtime_image_id="sha256:" + ("b" * 64),
            warnings=(),
        )
        return DocumentOCRResult(
            document_format=descriptor.document_format,
            ocr_version=self.OCR_VERSION,
            source_sha256=descriptor.content_sha256,
            classification=descriptor.classification.value,
            render_dpi=200,
            page_count=1,
            selected_page_count=1,
            total_characters=len(text),
            ocr_text_sha256=_sha256(text),
            pages=(page,),
            warnings=(),
        )


@pytest.fixture
def resolver_context(tmp_path: Path):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    for workspace_id in ("workspace_alpha", "workspace_beta"):
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
    event_bus = EventBus()
    registry = DocumentRegistryService(
        session=session,
        event_bus=event_bus,
        storage=ManagedDocumentStorage(tmp_path / "documents"),
    )
    extraction = DocumentExtractionService(
        session=session,
        event_bus=event_bus,
        registry=registry,
    )
    ocr = DocumentOCRService(
        session=session,
        event_bus=event_bus,
        registry=registry,
        ocr=OnePageOCR(),
    )
    resolver = DocumentAIContextResolver(session=session)

    yield session, registry, extraction, ocr, resolver

    session.close()
    engine.dispose()


async def upload(
    registry: DocumentRegistryService,
    *,
    filename: str,
    content_type: str,
    content: bytes,
):
    return await registry.upload(
        request=DocumentIntakeRequest(
            workspace_id="workspace_alpha",
            filename=filename,
            content_type=content_type,
            classification=DataClassification.CONFIDENTIAL,
        ),
        content=content,
        actor_id="operator_alpha",
        now=NOW,
    )


@pytest.mark.asyncio
async def test_resolver_reads_only_exact_extraction_unit_and_chunk(
    resolver_context,
) -> None:
    _, registry, extraction, _, resolver = resolver_context
    uploaded = await upload(
        registry,
        filename="evidence.txt",
        content_type="text/plain",
        content=TXT_BYTES,
    )
    completed = await extraction.extract(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        actor_id="operator_alpha",
        now=NOW,
    )
    units = extraction.list_units(
        run_id=completed.record.id,
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
    )
    chunks = extraction.list_chunks(
        run_id=completed.record.id,
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
    )
    selection = DocumentContextSelection(
        workspace_id="workspace_alpha",
        sources=(
            DocumentContextSourceRef(
                document_id=uploaded.record.id,
                run_id=completed.record.id,
                source_kind=DocumentContextSourceKind.EXTRACTION_UNIT,
                source_id=units[0].id,
            ),
            DocumentContextSourceRef(
                document_id=uploaded.record.id,
                run_id=completed.record.id,
                source_kind=DocumentContextSourceKind.EXTRACTION_CHUNK,
                source_id=chunks[0].id,
            ),
        ),
    )

    sources = resolver.resolve(selection=selection, now=NOW)

    assert [source.source_id for source in sources] == [
        units[0].id,
        chunks[0].id,
    ]
    assert all(
        source.classification is DataClassification.CONFIDENTIAL
        for source in sources
    )
    assert sources[0].locators[0].kind is (
        DocumentCitationLocationKind.TXT_DOCUMENT
    )
    assert sources[1].locators[0].unit_ordinal == 0


@pytest.mark.asyncio
async def test_resolver_rejects_tampered_persisted_text(
    resolver_context,
) -> None:
    session, registry, extraction, _, resolver = resolver_context
    uploaded = await upload(
        registry,
        filename="evidence.txt",
        content_type="text/plain",
        content=TXT_BYTES,
    )
    completed = await extraction.extract(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        now=NOW,
    )
    unit = extraction.list_units(
        run_id=completed.record.id,
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
    )[0]
    stored = session.get(DocumentExtractionUnitModel, unit.id)
    assert stored is not None
    stored.text = "tampered text"
    session.flush()
    selection = DocumentContextSelection(
        workspace_id="workspace_alpha",
        sources=(
            DocumentContextSourceRef(
                document_id=uploaded.record.id,
                run_id=completed.record.id,
                source_kind=DocumentContextSourceKind.EXTRACTION_UNIT,
                source_id=unit.id,
            ),
        ),
    )

    with pytest.raises(DocumentAIAnalysisError) as captured:
        resolver.resolve(selection=selection, now=NOW)

    assert captured.value.code == "DOCUMENT_AI_SOURCE_HASH_MISMATCH"
    assert "tampered text" not in str(captured.value.details)


@pytest.mark.asyncio
async def test_resolver_hides_cross_workspace_source(resolver_context) -> None:
    _, registry, extraction, _, resolver = resolver_context
    uploaded = await upload(
        registry,
        filename="evidence.txt",
        content_type="text/plain",
        content=TXT_BYTES,
    )
    completed = await extraction.extract(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        now=NOW,
    )
    unit = extraction.list_units(
        run_id=completed.record.id,
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
    )[0]
    selection = DocumentContextSelection(
        workspace_id="workspace_beta",
        sources=(
            DocumentContextSourceRef(
                document_id=uploaded.record.id,
                run_id=completed.record.id,
                source_kind=DocumentContextSourceKind.EXTRACTION_UNIT,
                source_id=unit.id,
            ),
        ),
    )

    with pytest.raises(DocumentAIAnalysisError) as captured:
        resolver.resolve(selection=selection, now=NOW)

    assert captured.value.code == "DOCUMENT_AI_SOURCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_resolver_reads_active_unexpired_ocr_page(resolver_context) -> None:
    _, registry, _, ocr, resolver = resolver_context
    uploaded = await upload(
        registry,
        filename="scan.pdf",
        content_type="application/pdf",
        content=PDF_BYTES,
    )
    completed = await ocr.recognize(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        retention_days=2,
        now=NOW,
    )
    page = ocr.list_pages(
        run_id=completed.record.id,
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        now=NOW,
    )[0]
    selection = DocumentContextSelection(
        workspace_id="workspace_alpha",
        sources=(
            DocumentContextSourceRef(
                document_id=uploaded.record.id,
                run_id=completed.record.id,
                source_kind=DocumentContextSourceKind.OCR_PAGE,
                source_id=page.id,
            ),
        ),
    )

    sources = resolver.resolve(selection=selection, now=NOW)

    assert sources[0].text == "OCR evidence on page one."
    assert sources[0].locators[0].kind is (
        DocumentCitationLocationKind.OCR_PAGE
    )
    assert sources[0].locators[0].page_number == 1


@pytest.mark.asyncio
async def test_resolver_rejects_expired_or_purged_ocr_text(
    resolver_context,
) -> None:
    session, registry, _, ocr, resolver = resolver_context
    uploaded = await upload(
        registry,
        filename="scan.pdf",
        content_type="application/pdf",
        content=PDF_BYTES,
    )
    completed = await ocr.recognize(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        retention_days=1,
        now=NOW,
    )
    page = ocr.list_pages(
        run_id=completed.record.id,
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        now=NOW,
    )[0]
    row = session.get(DocumentOCRRunModel, completed.record.id)
    assert row is not None
    row.retention_expires_at = NOW - timedelta(seconds=1)
    session.flush()
    selection = DocumentContextSelection(
        workspace_id="workspace_alpha",
        sources=(
            DocumentContextSourceRef(
                document_id=uploaded.record.id,
                run_id=completed.record.id,
                source_kind=DocumentContextSourceKind.OCR_PAGE,
                source_id=page.id,
            ),
        ),
    )

    with pytest.raises(DocumentAIAnalysisError) as captured:
        resolver.resolve(selection=selection, now=NOW)

    assert captured.value.code == "DOCUMENT_AI_OCR_TEXT_UNAVAILABLE"
