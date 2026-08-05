from __future__ import annotations

import json
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
    get_document_registry_service,
    get_workspace_policy_service,
)
from backend.control_center.governance_schemas import (
    HumanControlPermission,
)
from backend.control_center.security import (
    HumanControlPrincipal,
    get_request_principal,
)
from backend.core.events import EventBus
from backend.database.base import Base
from backend.database.models import WorkspaceModel
from backend.documents import (
    DocumentRegistryService,
    ManagedDocumentStorage,
)
from backend.routers import documents
from backend.runtime_policy import DataClassification


PDF_BYTES = b"%PDF-1.7\napi test\n%%EOF"


def add_workspace(
    session: Session,
    workspace_id: str,
) -> None:
    session.add(
        WorkspaceModel(
            id=workspace_id,
            name=f"Documents {workspace_id}",
            description="",
            workspace_type="documents",
            status="active",
            metadata_json={},
        )
    )
    session.flush()


def make_principal(
    workspace_id: str,
    *,
    actor_id: str = "operator_alpha",
    permissions: set[str] | None = None,
) -> HumanControlPrincipal:
    effective_permissions = permissions or {
        HumanControlPermission.DOCUMENT_VIEW.value,
        HumanControlPermission.DOCUMENT_UPLOAD.value,
        HumanControlPermission.DOCUMENT_DELETE.value,
        HumanControlPermission.DOCUMENT_AUDIT.value,
    }
    return HumanControlPrincipal.from_payload(
        {
            "identity_id": f"identity_{actor_id}",
            "actor_id": actor_id,
            "username": actor_id,
            "display_name": actor_id,
            "identity_type": "human",
            "workspace_id": workspace_id,
            "auth_method": "browser_session",
            "scopes": ["*"],
            "credential_id": f"credential_{actor_id}",
        },
        governed=True,
        role_keys=("operator",),
        permissions=effective_permissions,
    )


def upload_request(
    client: TestClient,
    base: str,
    *,
    filename: str = "report.pdf",
    payload: bytes = PDF_BYTES,
    content_type: str = "application/pdf",
    metadata: dict | None = None,
):
    return client.post(
        base,
        files={
            "file": (
                filename,
                payload,
                content_type,
            )
        },
        data={
            "metadata_json": json.dumps(
                metadata or {},
                ensure_ascii=False,
            )
        },
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
    principal = {
        "value": make_principal("workspace_alpha"),
    }
    policy = {
        "classification": DataClassification.CONFIDENTIAL,
    }

    class PolicyServiceStub:
        def get_effective_policy(
            self,
            workspace_id: str,
        ):
            if workspace_id not in {
                "workspace_alpha",
                "workspace_beta",
            }:
                raise ValueError("Workspace does not exist.")
            return SimpleNamespace(
                data_classification=policy["classification"]
            )

    container = SimpleNamespace(
        human_control_auth_service=None,
    )

    app = FastAPI()
    app.include_router(
        documents.router,
        prefix="/api",
    )

    async def principal_override() -> HumanControlPrincipal:
        return principal["value"]

    app.dependency_overrides[
        get_request_principal
    ] = principal_override
    app.dependency_overrides[
        get_document_registry_service
    ] = lambda: registry
    app.dependency_overrides[
        get_workspace_policy_service
    ] = PolicyServiceStub
    app.dependency_overrides[
        get_container
    ] = lambda: container

    with TestClient(app) as client:
        yield client, registry, principal, policy

    session.close()
    engine.dispose()


def test_upload_uses_workspace_policy_and_actor_binding(
    api_context,
) -> None:
    client, _, _, _ = api_context
    base = "/api/workspaces/workspace_alpha/documents"

    response = upload_request(
        client,
        base,
        metadata={"source": "manual"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["created"] is True
    assert body["restored"] is False
    assert body["storage_created"] is True
    document = body["document"]
    assert document["workspace_id"] == "workspace_alpha"
    assert document["classification"] == "confidential"
    assert document["uploaded_by"] == "operator_alpha"
    assert document["metadata"] == {"source": "manual"}
    assert "storage_key" not in document


def test_same_upload_is_idempotent(api_context) -> None:
    client, _, _, _ = api_context
    base = "/api/workspaces/workspace_alpha/documents"

    first = upload_request(client, base)
    second = upload_request(client, base)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert (
        second.json()["document"]["id"]
        == first.json()["document"]["id"]
    )


def test_list_and_get_are_workspace_scoped(
    api_context,
) -> None:
    client, _, principal, _ = api_context
    alpha_base = (
        "/api/workspaces/workspace_alpha/documents"
    )
    created = upload_request(client, alpha_base)
    document_id = created.json()["document"]["id"]

    listed = client.get(alpha_base)
    assert listed.status_code == 200, listed.text
    assert [
        item["id"]
        for item in listed.json()["items"]
    ] == [document_id]

    fetched = client.get(
        f"{alpha_base}/{document_id}"
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["id"] == document_id

    mismatch = client.get(
        "/api/workspaces/workspace_beta/documents"
    )
    assert mismatch.status_code == 403

    principal["value"] = make_principal(
        "workspace_beta",
        actor_id="operator_beta",
    )
    hidden = client.get(
        "/api/workspaces/workspace_beta/documents/"
        f"{document_id}"
    )
    assert hidden.status_code == 404


def test_delete_requires_document_delete_permission(
    api_context,
) -> None:
    client, _, principal, _ = api_context
    base = "/api/workspaces/workspace_alpha/documents"
    created = upload_request(client, base)
    document_id = created.json()["document"]["id"]

    principal["value"] = make_principal(
        "workspace_alpha",
        permissions={
            HumanControlPermission.DOCUMENT_VIEW.value,
            HumanControlPermission.DOCUMENT_UPLOAD.value,
        },
    )
    denied = client.delete(
        f"{base}/{document_id}"
    )
    assert denied.status_code == 403

    principal["value"] = make_principal(
        "workspace_alpha",
    )
    deleted = client.delete(
        f"{base}/{document_id}"
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True
    assert deleted.json()["document"]["status"] == "deleted"

    hidden = client.get(
        f"{base}/{document_id}"
    )
    assert hidden.status_code == 404


def test_events_require_audit_permission_and_do_not_leak_content(
    api_context,
) -> None:
    client, _, principal, _ = api_context
    base = "/api/workspaces/workspace_alpha/documents"
    created = upload_request(client, base)
    document_id = created.json()["document"]["id"]

    principal["value"] = make_principal(
        "workspace_alpha",
        permissions={
            HumanControlPermission.DOCUMENT_VIEW.value,
        },
    )
    denied = client.get(
        f"{base}/{document_id}/events"
    )
    assert denied.status_code == 403

    principal["value"] = make_principal(
        "workspace_alpha",
    )
    response = client.get(
        f"{base}/{document_id}/events"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [
        item["event_type"]
        for item in body["items"]
    ] == ["document.uploaded"]
    assert "api test" not in response.text
    assert "raw_content" not in response.text


@pytest.mark.parametrize(
    "metadata_json",
    [
        "{invalid",
        "[]",
    ],
)
def test_invalid_metadata_json_is_rejected(
    api_context,
    metadata_json: str,
) -> None:
    client, _, _, _ = api_context
    response = client.post(
        "/api/workspaces/workspace_alpha/documents",
        files={
            "file": (
                "report.pdf",
                PDF_BYTES,
                "application/pdf",
            )
        },
        data={"metadata_json": metadata_json},
    )

    assert response.status_code == 422


def test_sensitive_metadata_key_is_rejected(
    api_context,
) -> None:
    client, _, _, _ = api_context
    response = upload_request(
        client,
        "/api/workspaces/workspace_alpha/documents",
        metadata={"raw_content": "blocked"},
    )

    assert response.status_code == 422
    assert "Unsafe document metadata key" in response.text


def test_mime_mismatch_is_rejected(
    api_context,
) -> None:
    client, _, _, _ = api_context
    response = upload_request(
        client,
        "/api/workspaces/workspace_alpha/documents",
        content_type="text/plain",
    )

    assert response.status_code == 422


def test_oversized_upload_is_rejected_before_registry(
    api_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, registry, _, _ = api_context
    monkeypatch.setattr(
        documents,
        "_MAX_UPLOAD_BYTES",
        8,
    )

    response = upload_request(
        client,
        "/api/workspaces/workspace_alpha/documents",
        payload=b"%PDF-1.7\nlarge",
    )

    assert response.status_code == 413
    assert registry.list(
        workspace_id="workspace_alpha"
    ) == []


def test_documents_routes_are_in_openapi(
    api_context,
) -> None:
    client, _, _, _ = api_context
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    base = "/api/workspaces/{workspace_id}/documents"
    assert base in paths
    assert f"{base}/{{document_id}}" in paths
    assert f"{base}/{{document_id}}/events" in paths
