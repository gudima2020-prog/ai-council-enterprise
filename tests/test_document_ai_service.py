from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.core.events import EventBus
from backend.database.base import Base
from backend.database.models import ModelConfigModel, WorkspaceModel
from backend.documents.ai_analysis import (
    DocumentAIAnalysisError,
    DocumentAIWorkflow,
)
from backend.documents.ai_context import (
    DocumentContextSelection,
    DocumentContextSourceKind,
    DocumentContextSourceRef,
)
from backend.documents.ai_service import (
    DocumentAIAnalysisService,
    DocumentAIApprovalCredentials,
    DocumentAIRequestSpec,
)
from backend.documents.extraction_service import DocumentExtractionService
from backend.documents.intake import DocumentIntakeRequest
from backend.documents.service import DocumentRegistryService
from backend.documents.storage import ManagedDocumentStorage
from backend.gateway.schemas import GatewayError, GatewayResponse
from backend.runtime_policy import DataClassification, ProviderTrust

NOW = datetime(2026, 8, 6, 11, 0, tzinfo=timezone.utc)
DOCUMENT_BYTES = b"The governed deadline is 30 June 2027."


class PolicyStub:
    def __init__(self, trust: ProviderTrust) -> None:
        self.trust = trust

    def get_effective_policy(self, workspace_id: str):
        assert workspace_id == "workspace_alpha"
        return SimpleNamespace(
            temperature=0.2,
            provider_trust_for=lambda provider: self.trust,
        )


class FakeGateway:
    def __init__(
        self,
        *,
        primary_content: str | None = None,
        reviewer_content: str | None = None,
        approval_required: bool = False,
    ) -> None:
        self.primary_content = primary_content or json.dumps(
            {
                "answer": "The deadline is 30 June 2027.",
                "citation_ids": ["D1"],
            }
        )
        self.reviewer_content = reviewer_content or json.dumps(
            {
                "approved": True,
                "citation_coverage": "complete",
                "policy_compliant": True,
                "unsupported_citation_ids": [],
                "reason_codes": [],
            }
        )
        self.approval_required = approval_required
        self.calls: list[dict] = []

    async def ask(self, **kwargs) -> GatewayResponse:
        self.calls.append(dict(kwargs))
        stage = "primary" if kwargs["model"] == "model-a" else "reviewer"
        if self.approval_required and not kwargs.get("approval_id"):
            return GatewayResponse(
                request_id=kwargs["request_id"],
                provider=kwargs["provider"],
                model=kwargs["model"],
                content="",
                status="error",
                error=GatewayError(
                    code="POLICY_APPROVAL_REQUIRED",
                    message="approval required",
                    provider=kwargs["provider"],
                    recoverable=False,
                ),
                metadata={
                    "runtime_policy": {
                        "policy_version": "test",
                        "action": "require_approval",
                        "reason_codes": ["TEST_APPROVAL_REQUIRED"],
                        "fingerprint": "a" * 64,
                        "data_classification": "confidential",
                        "provider_trust": "trusted_external",
                    },
                    "policy_approval": {
                        "approval_id": f"approval_{stage}",
                        "status": "pending",
                        "request_id": kwargs["request_id"],
                        "scope_fingerprint": "b" * 64,
                        "expires_at": "2026-08-06T12:00:00+00:00",
                    },
                },
            )
        content = (
            self.primary_content
            if stage == "primary"
            else self.reviewer_content
        )
        return GatewayResponse(
            request_id=kwargs["request_id"],
            provider=kwargs["provider"],
            model=kwargs["model"],
            content=content,
            status="success",
            latency_ms=10.0,
            cost=0.01,
            metadata={
                "runtime_policy": {
                    "policy_version": "test",
                    "action": (
                        "require_approval"
                        if self.approval_required
                        else "allow"
                    ),
                    "reason_codes": ["TEST_ALLOWED"],
                    "fingerprint": "a" * 64,
                    "data_classification": kwargs["data_classification"],
                    "provider_trust": (
                        "trusted_external"
                        if self.approval_required
                        else "external"
                    ),
                },
                "policy_approval": (
                    {
                        "approval_id": kwargs["approval_id"],
                        "status": "consumed",
                        "request_id": kwargs["request_id"],
                        "scope_fingerprint": "b" * 64,
                        "consumed_at": "2026-08-06T11:00:00+00:00",
                    }
                    if kwargs.get("approval_id")
                    else {}
                ),
            },
        )


@pytest.fixture
def service_context(tmp_path: Path):
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
    for provider, slug in (
        ("provider-a", "model-a"),
        ("provider-b", "model-b"),
    ):
        session.add(
            ModelConfigModel(
                id=f"id-{slug}",
                provider=provider,
                slug=slug,
                display_name=slug,
                enabled=True,
                priority=1,
                context_window=100_000,
                max_output_tokens=4_096,
                supports_tools=False,
                supports_vision=False,
                supports_json=True,
                metadata_json={},
                created_at=NOW,
                updated_at=NOW,
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

    yield session, event_bus, registry, extraction

    session.close()
    engine.dispose()


async def make_spec(
    registry: DocumentRegistryService,
    extraction: DocumentExtractionService,
    *,
    classification: DataClassification,
) -> DocumentAIRequestSpec:
    uploaded = await registry.upload(
        request=DocumentIntakeRequest(
            workspace_id="workspace_alpha",
            filename="deadline.txt",
            content_type="text/plain",
            classification=classification,
        ),
        content=DOCUMENT_BYTES,
        actor_id="operator_alpha",
        now=NOW,
    )
    completed = await extraction.extract(
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
        actor_id="operator_alpha",
        now=NOW,
    )
    unit = extraction.list_units(
        run_id=completed.record.id,
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
    )[0]
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
    return DocumentAIRequestSpec(
        workspace_id="workspace_alpha",
        workflow=DocumentAIWorkflow.QUESTION,
        selection=selection,
        provider="provider-a",
        model="model-a",
        reviewer_provider="provider-b",
        reviewer_model="model-b",
        question="What is the governed deadline?",
        max_output_tokens=256,
        reviewer_max_output_tokens=128,
    )


@pytest.mark.asyncio
async def test_preflight_warns_before_external_gateway_request(
    service_context,
) -> None:
    session, event_bus, registry, extraction = service_context
    gateway = FakeGateway()
    spec = await make_spec(
        registry,
        extraction,
        classification=DataClassification.INTERNAL,
    )
    service = DocumentAIAnalysisService(
        session=session,
        event_bus=event_bus,
        gateway=gateway,
        workspace_policy=PolicyStub(ProviderTrust.EXTERNAL),
    )

    result = service.preflight(spec=spec, now=NOW)

    assert gateway.calls == []
    assert result.external_warning_required is True
    assert result.to_public_dict()["context"]["manifest"][
        "effective_classification"
    ] == "internal"
    assert "packed_context" not in str(result.to_public_dict())


@pytest.mark.asyncio
async def test_execution_requires_explicit_external_acknowledgement(
    service_context,
) -> None:
    session, event_bus, registry, extraction = service_context
    gateway = FakeGateway()
    spec = await make_spec(
        registry,
        extraction,
        classification=DataClassification.INTERNAL,
    )
    service = DocumentAIAnalysisService(
        session=session,
        event_bus=event_bus,
        gateway=gateway,
        workspace_policy=PolicyStub(ProviderTrust.EXTERNAL),
    )

    with pytest.raises(DocumentAIAnalysisError) as captured:
        await service.execute(
            spec=spec,
            idempotency_key="request-0001",
            external_provider_acknowledged=False,
            now=NOW,
        )

    assert captured.value.code == "DOCUMENT_AI_EXTERNAL_ACK_REQUIRED"
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_primary_and_independent_reviewer_complete_with_evidence(
    service_context,
) -> None:
    session, event_bus, registry, extraction = service_context
    gateway = FakeGateway()
    spec = await make_spec(
        registry,
        extraction,
        classification=DataClassification.INTERNAL,
    )
    seen = []

    async def capture(event):
        seen.append(event)

    event_bus.subscribe("document.ai.*", capture)
    service = DocumentAIAnalysisService(
        session=session,
        event_bus=event_bus,
        gateway=gateway,
        workspace_policy=PolicyStub(ProviderTrust.EXTERNAL),
    )

    result = await service.execute(
        spec=spec,
        idempotency_key="request-0002",
        external_provider_acknowledged=True,
        actor_id="operator_alpha",
        now=NOW,
    )

    assert result.record.row.status == "completed"
    assert result.record.row.output_text == "The deadline is 30 June 2027."
    assert result.record.row.output_citation_ids_json == ["D1"]
    assert result.record.row.reviewer_verdict_json["approved"] is True
    assert result.record.row.provider_evidence_json["primary"]["cost"] == 0.01
    assert [(call["provider"], call["model"]) for call in gateway.calls] == [
        ("provider-a", "model-a"),
        ("provider-b", "model-b"),
    ]
    assert all(call["data_classification"] == "internal" for call in gateway.calls)
    assert [event.event_type for event in seen] == [
        "document.ai.created",
        "document.ai.primary_completed",
        "document.ai.completed",
    ]
    event_payloads = str([event.payload for event in seen])
    assert "30 June 2027" not in event_payloads
    assert "governed deadline" not in event_payloads


@pytest.mark.asyncio
async def test_idempotency_reuses_completed_run_and_conflicts_on_change(
    service_context,
) -> None:
    session, event_bus, registry, extraction = service_context
    gateway = FakeGateway()
    spec = await make_spec(
        registry,
        extraction,
        classification=DataClassification.INTERNAL,
    )
    service = DocumentAIAnalysisService(
        session=session,
        event_bus=event_bus,
        gateway=gateway,
        workspace_policy=PolicyStub(ProviderTrust.EXTERNAL),
    )
    first = await service.execute(
        spec=spec,
        idempotency_key="request-0003",
        external_provider_acknowledged=True,
        now=NOW,
    )
    repeated = await service.execute(
        spec=spec,
        idempotency_key="request-0003",
        external_provider_acknowledged=True,
        now=NOW,
    )

    assert repeated.reused is True
    assert repeated.record.row.id == first.record.row.id
    assert len(gateway.calls) == 2

    changed = DocumentAIRequestSpec(
        workspace_id=spec.workspace_id,
        workflow=spec.workflow,
        selection=spec.selection,
        provider=spec.provider,
        model=spec.model,
        reviewer_provider=spec.reviewer_provider,
        reviewer_model=spec.reviewer_model,
        question=spec.question,
        max_output_tokens=257,
        reviewer_max_output_tokens=spec.reviewer_max_output_tokens,
    )
    with pytest.raises(DocumentAIAnalysisError) as captured:
        await service.execute(
            spec=changed,
            idempotency_key="request-0003",
            external_provider_acknowledged=True,
            now=NOW,
        )
    assert captured.value.code == "DOCUMENT_AI_IDEMPOTENCY_CONFLICT"


@pytest.mark.asyncio
async def test_unsupported_primary_citation_fails_before_reviewer(
    service_context,
) -> None:
    session, event_bus, registry, extraction = service_context
    gateway = FakeGateway(
        primary_content=json.dumps(
            {"answer": "Invented.", "citation_ids": ["D999"]}
        )
    )
    spec = await make_spec(
        registry,
        extraction,
        classification=DataClassification.INTERNAL,
    )
    service = DocumentAIAnalysisService(
        session=session,
        event_bus=event_bus,
        gateway=gateway,
        workspace_policy=PolicyStub(ProviderTrust.EXTERNAL),
    )

    result = await service.execute(
        spec=spec,
        idempotency_key="request-0004",
        external_provider_acknowledged=True,
        now=NOW,
    )

    assert result.record.row.status == "failed"
    assert result.record.row.error_code == "DOCUMENT_AI_CITATION_UNSUPPORTED"
    assert len(gateway.calls) == 1
    assert "Invented." not in str(result.record.row.error_details_json)


@pytest.mark.asyncio
async def test_primary_and_reviewer_use_separate_one_time_approvals(
    service_context,
) -> None:
    session, event_bus, registry, extraction = service_context
    gateway = FakeGateway(approval_required=True)
    spec = await make_spec(
        registry,
        extraction,
        classification=DataClassification.CONFIDENTIAL,
    )
    service = DocumentAIAnalysisService(
        session=session,
        event_bus=event_bus,
        gateway=gateway,
        workspace_policy=PolicyStub(ProviderTrust.TRUSTED_EXTERNAL),
    )

    primary_pending = await service.execute(
        spec=spec,
        idempotency_key="request-0005",
        external_provider_acknowledged=True,
        now=NOW,
    )
    assert primary_pending.approval_stage == "primary"
    assert primary_pending.record.row.status == "awaiting_primary_approval"
    call_count = len(gateway.calls)
    same_pending = await service.execute(
        spec=spec,
        idempotency_key="request-0005",
        external_provider_acknowledged=True,
        now=NOW,
    )
    assert same_pending.reused is True
    assert same_pending.approval_stage == "primary"
    assert len(gateway.calls) == call_count

    reviewer_pending = await service.execute(
        spec=spec,
        idempotency_key="request-0005",
        external_provider_acknowledged=True,
        primary_approval=DocumentAIApprovalCredentials(
            approval_id="approval_primary",
            token="primary-token",
        ),
        now=NOW,
    )
    assert reviewer_pending.approval_stage == "reviewer"
    assert reviewer_pending.record.row.status == "awaiting_reviewer_approval"
    assert "output_text" not in reviewer_pending.to_public_dict()["analysis"]

    completed = await service.execute(
        spec=spec,
        idempotency_key="request-0005",
        external_provider_acknowledged=True,
        reviewer_approval=DocumentAIApprovalCredentials(
            approval_id="approval_reviewer",
            token="reviewer-token",
        ),
        now=NOW,
    )
    assert completed.record.row.status == "completed"
    assert completed.to_public_dict()["analysis"]["output_text"] == (
        "The deadline is 30 June 2027."
    )
    assert completed.record.row.approval_evidence_json["primary"][
        "approval_id"
    ] == "approval_primary"
    assert completed.record.row.approval_evidence_json["reviewer"][
        "approval_id"
    ] == "approval_reviewer"
    assert "primary-token" not in str(
        completed.record.row.approval_evidence_json
    )
    assert "reviewer-token" not in str(
        completed.record.row.approval_evidence_json
    )


@pytest.mark.asyncio
async def test_runtime_policy_denial_prevents_gateway_call(
    service_context,
) -> None:
    session, event_bus, registry, extraction = service_context
    gateway = FakeGateway()
    spec = await make_spec(
        registry,
        extraction,
        classification=DataClassification.CONFIDENTIAL,
    )
    service = DocumentAIAnalysisService(
        session=session,
        event_bus=event_bus,
        gateway=gateway,
        workspace_policy=PolicyStub(ProviderTrust.EXTERNAL),
    )

    with pytest.raises(DocumentAIAnalysisError) as captured:
        await service.execute(
            spec=spec,
            idempotency_key="request-0006",
            external_provider_acknowledged=True,
            now=NOW,
        )

    assert captured.value.code == "DOCUMENT_AI_POLICY_DENIED"
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_independent_reviewer_can_reject_and_document_delete_cleans_run(
    service_context,
) -> None:
    session, event_bus, registry, extraction = service_context
    gateway = FakeGateway(
        reviewer_content=json.dumps(
            {
                "approved": False,
                "citation_coverage": "partial",
                "policy_compliant": True,
                "unsupported_citation_ids": [],
                "reason_codes": ["CITATION_COVERAGE_PARTIAL"],
            }
        )
    )
    spec = await make_spec(
        registry,
        extraction,
        classification=DataClassification.INTERNAL,
    )
    service = DocumentAIAnalysisService(
        session=session,
        event_bus=event_bus,
        gateway=gateway,
        workspace_policy=PolicyStub(ProviderTrust.EXTERNAL),
    )
    rejected = await service.execute(
        spec=spec,
        idempotency_key="request-0007",
        external_provider_acknowledged=True,
        now=NOW,
    )
    assert rejected.record.row.status == "rejected"
    assert rejected.record.row.error_code == "DOCUMENT_AI_REVIEW_REJECTED"
    assert rejected.record.row.output_text == "The deadline is 30 June 2027."
    assert "output_text" not in rejected.to_public_dict()["analysis"]

    document_id = spec.selection.sources[0].document_id
    deleted = await registry.delete(
        document_id=document_id,
        workspace_id="workspace_alpha",
        actor_id="operator_alpha",
        now=NOW,
    )
    assert deleted.derived_deleted["ai_runs"] == 1
    assert deleted.derived_deleted["ai_citations"] == 1
    assert service.list(workspace_id="workspace_alpha") == []
