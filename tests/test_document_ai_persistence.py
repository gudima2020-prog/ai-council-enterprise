from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.database.base import Base
from backend.database.models import WorkspaceModel
from backend.documents.ai_context import (
    DocumentCitationLocationKind,
    DocumentCitationLocator,
    DocumentContextCitation,
    DocumentContextSourceKind,
)
from backend.documents.ai_repository import DocumentAIAnalysisRepository
from backend.documents.models import (
    DocumentAIAnalysisCitationModel,
    DocumentAIAnalysisRunModel,
    DocumentModel,
)
from backend.runtime_policy import DataClassification

NOW = datetime(2026, 8, 6, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def persistence_context():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(
        WorkspaceModel(
            id="workspace_alpha",
            name="workspace_alpha",
            description="",
            workspace_type="documents",
            status="active",
            metadata_json={},
        )
    )
    for index in (1, 2):
        session.add(
            DocumentModel(
                id=f"document_{index}",
                workspace_id="workspace_alpha",
                original_filename=f"document-{index}.txt",
                safe_filename=f"document-{index}.txt",
                document_format="txt",
                declared_mime_type="text/plain",
                detected_mime_type="text/plain",
                content_encoding="utf-8",
                size_bytes=10,
                content_sha256=str(index) * 64,
                intake_fingerprint=str(index + 2) * 64,
                classification="confidential",
                storage_key=f"sha256/{index}",
                storage_state="ready",
                status="active",
                uploaded_by="operator_alpha",
                deleted_by=None,
                deleted_at=None,
                metadata_json={},
                created_at=NOW,
                updated_at=NOW,
            )
        )
    session.flush()
    yield session, DocumentAIAnalysisRepository(session)
    session.close()
    engine.dispose()


def make_citation(
    citation_id: str,
    document_id: str,
) -> DocumentContextCitation:
    return DocumentContextCitation(
        citation_id=citation_id,
        document_id=document_id,
        run_id=f"extraction_{document_id}",
        source_kind=DocumentContextSourceKind.EXTRACTION_UNIT,
        source_id=f"unit_{document_id}",
        source_ordinal=0,
        classification=DataClassification.CONFIDENTIAL,
        source_text_sha256="a" * 64,
        fragment_text_sha256="b" * 64,
        fragment_index=1,
        fragment_count=1,
        character_start=0,
        character_end=10,
        estimated_tokens=10,
        locators=(
            DocumentCitationLocator(
                kind=DocumentCitationLocationKind.TXT_DOCUMENT,
                unit_ordinal=0,
            ),
        ),
    )


def create_run(
    repository: DocumentAIAnalysisRepository,
    *,
    idempotency_key: str = "idem-0001",
    expires_at: datetime = NOW + timedelta(days=30),
) -> DocumentAIAnalysisRunModel:
    return repository.create_pending(
        workspace_id="workspace_alpha",
        workflow="question",
        idempotency_key=idempotency_key,
        request_fingerprint="c" * 64,
        selection_fingerprint="d" * 64,
        selected_sources=[
            {
                "document_id": "document_1",
                "run_id": "extraction_document_1",
                "source_kind": "extraction_unit",
                "source_id": "unit_document_1",
            }
        ],
        context_manifest={"manifest_fingerprint": "e" * 64},
        context_sha256="f" * 64,
        effective_classification="confidential",
        primary_provider="provider-a",
        primary_model="model-a",
        primary_request_id="ai_req_primary",
        reviewer_provider="provider-b",
        reviewer_model="model-b",
        reviewer_request_id="ai_req_reviewer",
        provider_trust={
            "primary": "trusted_external",
            "reviewer": "trusted_external",
        },
        external_provider_acknowledged=True,
        request_text="What is the deadline?",
        request_text_sha256="1" * 64,
        retention_policy="classification-retention-v1",
        retention_days=30,
        retention_expires_at=expires_at,
        requested_by="operator_alpha",
        citations=(
            make_citation("D1", "document_1"),
            make_citation("D2", "document_2"),
        ),
        now=NOW,
    )


def test_repository_persists_metadata_only_context_and_exact_citations(
    persistence_context,
) -> None:
    session, repository = persistence_context
    row = create_run(repository)

    assert row.status == "pending"
    assert row.request_text == "What is the deadline?"
    assert "content" not in str(row.context_manifest_json)
    assert repository.find_idempotency(
        workspace_id="workspace_alpha",
        idempotency_key="idem-0001",
    ) is row
    citations = repository.list_citations(
        analysis_run_id=row.id,
        workspace_id="workspace_alpha",
    )
    assert [citation.citation_id for citation in citations] == ["D1", "D2"]
    assert session.scalar(
        select(func.count(DocumentAIAnalysisRunModel.id))
    ) == 1


def test_primary_result_marks_only_validated_citations_used(
    persistence_context,
) -> None:
    _, repository = persistence_context
    row = create_run(repository)

    repository.complete_primary(
        row,
        output_text="Validated answer.",
        output_text_sha256="2" * 64,
        citation_ids=("D2",),
        provider_evidence={"request_id": "ai_req_primary"},
        runtime_policy={"action": "allow"},
        approval_evidence={},
        now=NOW + timedelta(minutes=1),
    )

    assert row.status == "awaiting_reviewer"
    assert row.output_citation_ids_json == ["D2"]
    citations = repository.list_citations(
        analysis_run_id=row.id,
        workspace_id="workspace_alpha",
    )
    assert [citation.used_in_output for citation in citations] == [False, True]


def test_retention_purge_removes_raw_request_and_output_but_keeps_hashes(
    persistence_context,
) -> None:
    _, repository = persistence_context
    row = create_run(
        repository,
        expires_at=NOW - timedelta(seconds=1),
    )
    repository.complete_primary(
        row,
        output_text="Sensitive generated answer.",
        output_text_sha256="2" * 64,
        citation_ids=("D1",),
        provider_evidence={},
        runtime_policy={},
        approval_evidence={},
        now=NOW,
    )

    result = repository.purge_expired(
        workspace_id="workspace_alpha",
        now=NOW,
    )

    assert result == {"runs": 1, "run_ids": [row.id]}
    assert row.content_state == "purged"
    assert row.request_text is None
    assert row.output_text is None
    assert row.request_text_sha256 == "1" * 64
    assert row.output_text_sha256 == "2" * 64
    assert row.context_manifest_json["manifest_fingerprint"] == "e" * 64


def test_document_cleanup_deletes_whole_multi_document_analysis(
    persistence_context,
) -> None:
    session, repository = persistence_context
    row = create_run(repository)

    result = repository.delete_derived(
        document_id="document_2",
        workspace_id="workspace_alpha",
    )

    assert result == {"ai_runs": 1, "ai_citations": 2}
    assert session.get(DocumentAIAnalysisRunModel, row.id) is None
    assert session.scalar(
        select(func.count(DocumentAIAnalysisCitationModel.id))
    ) == 0
