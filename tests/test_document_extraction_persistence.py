from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.core.events import EventBus
from backend.database.base import Base
from backend.database.models import WorkspaceModel
from backend.documents.chunking import (
    DeterministicDocumentChunker,
    DocumentChunkingPolicy,
)
from backend.documents.extraction import (
    DocumentExtractionError,
)
from backend.documents.extraction_service import (
    DocumentExtractionConflictError,
    DocumentExtractionService,
)
from backend.documents.intake import (
    DocumentIntakeRequest,
)
from backend.documents.models import (
    DocumentExtractionChunkModel,
    DocumentExtractionRunModel,
    DocumentExtractionUnitModel,
)
from backend.documents.service import (
    DocumentRegistryService,
)
from backend.documents.storage import (
    ManagedDocumentStorage,
)
from backend.runtime_policy import DataClassification


def add_workspace(
    session: Session,
    workspace_id: str,
) -> None:
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


@pytest.fixture
def extraction_context(tmp_path: Path):
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
    service = DocumentExtractionService(
        session=session,
        event_bus=event_bus,
        registry=registry,
        chunker=DeterministicDocumentChunker(
            DocumentChunkingPolicy(
                max_chunk_characters=8,
                overlap_characters=2,
            )
        ),
    )

    yield session, registry, service, event_bus

    session.close()
    engine.dispose()


async def upload_txt(
    registry: DocumentRegistryService,
    *,
    workspace_id: str = "workspace_alpha",
    content: bytes = b"alpha beta gamma",
):
    return await registry.upload(
        request=DocumentIntakeRequest(
            workspace_id=workspace_id,
            filename="notes.txt",
            content_type="text/plain",
            classification=(
                DataClassification.CONFIDENTIAL
            ),
        ),
        content=content,
        actor_id="operator_alpha",
    )


@pytest.mark.asyncio
async def test_extraction_persists_run_units_and_chunks(
    extraction_context,
) -> None:
    session, registry, service, _ = extraction_context
    uploaded = await upload_txt(registry)

    result = await service.extract(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        actor_id="operator_alpha",
    )

    assert result.created is True
    assert result.reused is False
    assert result.record.status == "completed"
    assert result.record.unit_count == 1
    assert result.record.chunk_count == 3
    assert result.record.requested_by == "operator_alpha"
    assert result.record.attempt_count == 1

    units = service.list_units(
        run_id=result.record.id,
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
    )
    chunks = service.list_chunks(
        run_id=result.record.id,
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
    )

    assert [unit.text for unit in units] == [
        "alpha beta gamma"
    ]
    assert all(
        len(chunk.text) <= 8
        for chunk in chunks
    )
    assert session.scalar(
        select(
            func.count(
                DocumentExtractionUnitModel.id
            )
        )
    ) == 1
    assert session.scalar(
        select(
            func.count(
                DocumentExtractionChunkModel.id
            )
        )
    ) == 3


@pytest.mark.asyncio
async def test_completed_extraction_is_idempotent(
    extraction_context,
) -> None:
    _, registry, service, _ = extraction_context
    uploaded = await upload_txt(registry)

    first = await service.extract(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        actor_id="operator_alpha",
    )
    second = await service.extract(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        actor_id="operator_alpha",
    )

    assert second.created is False
    assert second.reused is True
    assert second.record.id == first.record.id
    assert second.record.attempt_count == 1


class FailingExtractor:
    PARSER_VERSION = "p3-001.3a-v1"

    def extract(self, **kwargs):
        raise DocumentExtractionError(
            "DOCUMENT_EXTRACTION_TEST_FAILURE",
            "Synthetic extraction failure.",
            details={"page_number": 2},
        )


@pytest.mark.asyncio
async def test_failure_evidence_is_persisted_without_content(
    extraction_context,
) -> None:
    session, registry, _, event_bus = extraction_context
    uploaded = await upload_txt(
        registry,
        content=b"secret source text",
    )
    service = DocumentExtractionService(
        session=session,
        event_bus=event_bus,
        registry=registry,
        extractor=FailingExtractor(),
    )

    result = await service.extract(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        actor_id="operator_alpha",
    )

    assert result.record.status == "failed"
    assert result.record.error_code == (
        "DOCUMENT_EXTRACTION_TEST_FAILURE"
    )
    assert result.record.error_details == {
        "page_number": 2
    }
    serialized = str(
        result.record.to_public_dict()
    )
    assert "secret source text" not in serialized
    assert result.record.unit_count == 0
    assert result.record.chunk_count == 0


@pytest.mark.asyncio
async def test_failed_exact_run_is_retried_in_place(
    extraction_context,
) -> None:
    session, registry, service, event_bus = (
        extraction_context
    )
    uploaded = await upload_txt(registry)

    failing = DocumentExtractionService(
        session=session,
        event_bus=event_bus,
        registry=registry,
        extractor=FailingExtractor(),
    )
    failed = await failing.extract(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        actor_id="operator_alpha",
    )
    completed = await service.extract(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        actor_id="operator_alpha",
    )

    assert completed.record.id == failed.record.id
    assert completed.record.status == "completed"
    assert completed.record.attempt_count == 2
    assert completed.record.error_code is None


@pytest.mark.asyncio
async def test_running_exact_run_conflicts(
    extraction_context,
) -> None:
    session, registry, service, _ = extraction_context
    uploaded = await upload_txt(registry)
    first = await service.extract(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        actor_id="operator_alpha",
    )
    row = session.get(
        DocumentExtractionRunModel,
        first.record.id,
    )
    assert row is not None
    row.status = "running"
    session.flush()

    with pytest.raises(
        DocumentExtractionConflictError
    ):
        await service.extract(
            document_id=uploaded.record.id,
            workspace_id="workspace_alpha",
            actor_id="operator_alpha",
        )


@pytest.mark.asyncio
async def test_workspace_isolation_hides_run(
    extraction_context,
) -> None:
    _, registry, service, _ = extraction_context
    uploaded = await upload_txt(registry)
    result = await service.extract(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
    )

    with pytest.raises(Exception) as captured:
        service.get(
            run_id=result.record.id,
            document_id=uploaded.record.id,
            workspace_id="workspace_beta",
        )

    assert "Workspace" in str(captured.value)


@pytest.mark.asyncio
async def test_document_delete_removes_derived_content(
    extraction_context,
) -> None:
    session, registry, service, _ = extraction_context
    uploaded = await upload_txt(registry)
    extracted = await service.extract(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
    )

    deleted = await registry.delete(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        actor_id="operator_alpha",
    )

    assert deleted.derived_deleted == {
        "runs": 1,
        "units": 1,
        "chunks": extracted.record.chunk_count,
    }
    assert session.scalar(
        select(
            func.count(
                DocumentExtractionRunModel.id
            )
        )
    ) == 0
    assert session.scalar(
        select(
            func.count(
                DocumentExtractionUnitModel.id
            )
        )
    ) == 0
    assert session.scalar(
        select(
            func.count(
                DocumentExtractionChunkModel.id
            )
        )
    ) == 0


@pytest.mark.asyncio
async def test_extraction_events_contain_metadata_only(
    extraction_context,
) -> None:
    _, registry, service, event_bus = extraction_context
    uploaded = await upload_txt(
        registry,
        content=b"private document body",
    )
    seen = []

    async def capture(event):
        seen.append(event)

    event_bus.subscribe(
        "document.extraction.*",
        capture,
    )

    await service.extract(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        actor_id="operator_alpha",
    )

    assert [
        event.event_type
        for event in seen
    ] == ["document.extraction.completed"]
    payload_text = str(seen[0].payload)
    assert "private document body" not in payload_text
    assert "text" not in seen[0].payload
