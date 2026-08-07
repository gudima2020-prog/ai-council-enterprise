from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.api.dependencies import (
    get_container,
    get_document_ai_analysis_service,
)
from backend.control_center.governance_schemas import HumanControlPermission
from backend.control_center.security import (
    HumanControlPrincipal,
    get_request_principal,
)
from backend.core.events import EventBus
from backend.database.base import Base
from backend.database.models import ModelConfigModel, WorkspaceModel
from backend.documents.ai_service import DocumentAIAnalysisService
from backend.documents.extraction_service import DocumentExtractionService
from backend.documents.intake import DocumentIntakeRequest
from backend.documents.service import DocumentRegistryService
from backend.documents.storage import ManagedDocumentStorage
from backend.routers import document_ai
from backend.runtime_policy import DataClassification, ProviderTrust
from tests.test_document_ai_service import FakeGateway, PolicyStub

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


def make_principal(
    workspace_id: str,
    *,
    permissions: set[str] | None = None,
) -> HumanControlPrincipal:
    return HumanControlPrincipal.from_payload(
        {
            "identity_id": "identity_operator",
            "actor_id": "operator_alpha",
            "username": "operator_alpha",
            "display_name": "Operator Alpha",
            "identity_type": "human",
            "workspace_id": workspace_id,
            "auth_method": "browser_session",
            "scopes": ["*"],
            "credential_id": "credential_operator",
        },
        governed=True,
        role_keys=("operator",),
        permissions=(
            permissions
            or {
                HumanControlPermission.DOCUMENT_AI.value,
                HumanControlPermission.RETENTION_MANAGE.value,
            }
        ),
    )


@pytest.fixture
def api_context(tmp_path: Path):
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
    uploaded = asyncio.run(
        registry.upload(
            request=DocumentIntakeRequest(
                workspace_id="workspace_alpha",
                filename="deadline.txt",
                content_type="text/plain",
                classification=DataClassification.INTERNAL,
            ),
            content=b"The deadline is 30 June 2027.",
            actor_id="operator_alpha",
            now=NOW,
        )
    )
    completed = asyncio.run(
        extraction.extract(
            document_id=uploaded.record.id,
            workspace_id="workspace_alpha",
            actor_id="operator_alpha",
            now=NOW,
        )
    )
    unit = extraction.list_units(
        run_id=completed.record.id,
        document_id=uploaded.record.id,
        workspace_id="workspace_alpha",
    )[0]
    gateway = FakeGateway()
    service = DocumentAIAnalysisService(
        session=session,
        event_bus=event_bus,
        gateway=gateway,
        workspace_policy=PolicyStub(ProviderTrust.EXTERNAL),
    )
    principal = {"value": make_principal("workspace_alpha")}

    app = FastAPI()
    app.include_router(document_ai.router, prefix="/api")

    async def principal_override():
        return principal["value"]

    app.dependency_overrides[get_request_principal] = principal_override
    app.dependency_overrides[get_document_ai_analysis_service] = lambda: service
    app.dependency_overrides[get_container] = lambda: SimpleNamespace(
        human_control_auth_service=None
    )

    payload = {
        "workflow": "question",
        "question": "What is the deadline?",
        "sources": [
            {
                "document_id": uploaded.record.id,
                "run_id": completed.record.id,
                "source_kind": "extraction_unit",
                "source_id": unit.id,
            }
        ],
        "provider": "provider-a",
        "model": "model-a",
        "reviewer_provider": "provider-b",
        "reviewer_model": "model-b",
        "max_output_tokens": 256,
        "reviewer_max_output_tokens": 128,
    }

    with TestClient(app) as client:
        yield client, payload, principal, gateway

    session.close()
    engine.dispose()


def test_preflight_execute_list_and_explicit_content(api_context) -> None:
    client, payload, _, gateway = api_context
    base = "/api/workspaces/workspace_alpha/document-ai"

    preflight = client.post(f"{base}/preflight", json=payload)
    assert preflight.status_code == 200, preflight.text
    assert preflight.headers["cache-control"] == "private, no-store"
    assert preflight.json()["external_provider_warning_required"] is True
    assert gateway.calls == []

    execute = client.post(
        f"{base}/runs",
        json={
            **payload,
            "idempotency_key": "api-request-0001",
            "external_provider_acknowledged": True,
        },
    )
    assert execute.status_code == 201, execute.text
    assert execute.headers["cache-control"] == "private, no-store"
    body = execute.json()
    assert body["analysis"]["status"] == "completed"
    assert body["analysis"]["output_text"] == (
        "The deadline is 30 June 2027."
    )
    run_id = body["analysis"]["id"]

    listed = client.get(f"{base}/runs")
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "private, no-store"
    assert listed.json()["items"][0]["id"] == run_id
    assert "output_text" not in listed.json()["items"][0]

    metadata = client.get(f"{base}/runs/{run_id}")
    assert metadata.status_code == 200
    assert metadata.headers["cache-control"] == "private, no-store"
    assert "output_text" not in metadata.json()
    content = client.get(
        f"{base}/runs/{run_id}",
        params={"include_content": "true"},
    )
    assert content.headers["cache-control"] == "private, no-store"
    assert content.json()["output_text"] == "The deadline is 30 June 2027."


def test_external_ack_permission_and_workspace_are_fail_closed(
    api_context,
) -> None:
    client, payload, principal, gateway = api_context
    base = "/api/workspaces/workspace_alpha/document-ai"
    missing_ack = client.post(
        f"{base}/runs",
        json={**payload, "idempotency_key": "api-request-0002"},
    )
    assert missing_ack.status_code == 428
    assert missing_ack.json()["detail"]["code"] == (
        "DOCUMENT_AI_EXTERNAL_ACK_REQUIRED"
    )
    assert gateway.calls == []

    principal["value"] = make_principal(
        "workspace_alpha",
        permissions={HumanControlPermission.DOCUMENT_VIEW.value},
    )
    denied = client.post(f"{base}/preflight", json=payload)
    assert denied.status_code == 403

    principal["value"] = make_principal("workspace_beta")
    mismatch = client.post(f"{base}/preflight", json=payload)
    assert mismatch.status_code == 403


def test_api_rejects_extra_fields_and_incomplete_approval(api_context) -> None:
    client, payload, _, _ = api_context
    base = "/api/workspaces/workspace_alpha/document-ai/runs"
    extra = client.post(
        base,
        json={
            **payload,
            "idempotency_key": "api-request-0003",
            "external_provider_acknowledged": True,
            "unexpected": "value",
        },
    )
    assert extra.status_code == 422
    incomplete = client.post(
        base,
        json={
            **payload,
            "idempotency_key": "api-request-0004",
            "external_provider_acknowledged": True,
            "primary_approval_id": "approval-only",
        },
    )
    assert incomplete.status_code == 422

    secret = "approval-secret-" + ("x" * 5000)
    oversized_secret = client.post(
        base,
        json={
            **payload,
            "idempotency_key": "api-request-0005",
            "external_provider_acknowledged": True,
            "primary_approval_id": "approval-primary",
            "primary_approval_token": secret,
        },
    )
    assert oversized_secret.status_code == 422
    assert secret not in oversized_secret.text


def test_document_ai_routes_are_in_openapi(api_context) -> None:
    client, _, _, _ = api_context
    paths = client.get("/openapi.json").json()["paths"]
    base = "/api/workspaces/{workspace_id}/document-ai"
    assert f"{base}/preflight" in paths
    assert f"{base}/runs" in paths
    assert f"{base}/runs/{{analysis_run_id}}" in paths
    assert f"{base}/retention/purge" in paths
