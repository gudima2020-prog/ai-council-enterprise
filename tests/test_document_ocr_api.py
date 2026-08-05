from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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
    get_document_ocr_service,
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
from backend.documents.models import DocumentOCRRunModel
from backend.documents.ocr_service import DocumentOCRService
from backend.documents.service import DocumentRegistryService
from backend.documents.storage import ManagedDocumentStorage
from backend.routers import documents
from backend.runtime_policy import DataClassification
from tests.test_document_ocr_persistence import (
    PDF_BYTES,
    FailingOCR,
    SuccessfulOCR,
)


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


def make_principal(
    workspace_id: str,
    *,
    actor_id: str = "operator_alpha",
    permissions: set[str] | None = None,
) -> HumanControlPrincipal:
    effective = permissions or {
        HumanControlPermission.DOCUMENT_VIEW.value,
        HumanControlPermission.DOCUMENT_UPLOAD.value,
        HumanControlPermission.DOCUMENT_OCR.value,
        HumanControlPermission.DOCUMENT_DELETE.value,
        HumanControlPermission.RETENTION_MANAGE.value,
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
        permissions=effective,
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
    ocr = {
        "value": DocumentOCRService(
            session=session,
            event_bus=event_bus,
            registry=registry,
            ocr=SuccessfulOCR(),
        )
    }
    principal = {
        "value": make_principal("workspace_alpha")
    }

    class PolicyServiceStub:
        def get_effective_policy(self, workspace_id: str):
            return SimpleNamespace(
                data_classification=(
                    DataClassification.CONFIDENTIAL
                )
            )

    app = FastAPI()
    app.include_router(documents.router, prefix="/api")

    async def principal_override():
        return principal["value"]

    app.dependency_overrides[
        get_request_principal
    ] = principal_override
    app.dependency_overrides[
        get_document_registry_service
    ] = lambda: registry
    app.dependency_overrides[
        get_document_ocr_service
    ] = lambda: ocr["value"]
    app.dependency_overrides[
        get_workspace_policy_service
    ] = PolicyServiceStub
    app.dependency_overrides[get_container] = lambda: (
        SimpleNamespace(human_control_auth_service=None)
    )

    with TestClient(app) as client:
        yield (
            client,
            session,
            registry,
            ocr,
            principal,
            event_bus,
        )

    session.close()
    engine.dispose()


def upload_pdf(client: TestClient) -> dict:
    response = client.post(
        "/api/workspaces/workspace_alpha/documents",
        files={
            "file": (
                "scan.pdf",
                PDF_BYTES,
                "application/pdf",
            )
        },
        data={"metadata_json": json.dumps({"source": "api"})},
    )
    assert response.status_code == 201, response.text
    return response.json()["document"]


def test_ocr_run_list_get_and_bounded_pages(
    api_context,
) -> None:
    client, _, _, _, _, _ = api_context
    document = upload_pdf(client)
    base = (
        "/api/workspaces/workspace_alpha/documents/"
        f"{document['id']}/ocr-runs"
    )
    created = client.post(
        base,
        json={
            "page_numbers": [3, 1],
            "retention_days": 5,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["created"] is True
    assert body["reused"] is False
    assert body["ocr_run"]["requested_pages"] == [1, 3]
    assert body["ocr_run"]["retention_days"] == 5
    assert "text" not in body["ocr_run"]
    run_id = body["ocr_run"]["id"]

    listed = client.get(base)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [
        run_id
    ]
    fetched = client.get(f"{base}/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == run_id

    metadata_pages = client.get(f"{base}/{run_id}/pages")
    assert metadata_pages.status_code == 200
    assert len(metadata_pages.json()["items"]) == 2
    assert "text" not in metadata_pages.json()["items"][0]

    text_pages = client.get(
        f"{base}/{run_id}/pages",
        params={"include_text": "true", "limit": 1},
    )
    assert text_pages.status_code == 200
    assert len(text_pages.json()["items"]) == 1
    assert text_pages.json()["items"][0]["text"] == "Page 1"


def test_repeat_ocr_reuses_completed_run(api_context) -> None:
    client, _, _, _, _, _ = api_context
    document = upload_pdf(client)
    base = (
        "/api/workspaces/workspace_alpha/documents/"
        f"{document['id']}/ocr-runs"
    )
    first = client.post(base, json={"page_numbers": [2, 1]})
    second = client.post(base, json={"page_numbers": [1, 2]})

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["reused"] is True
    assert second.json()["ocr_run"]["id"] == (
        first.json()["ocr_run"]["id"]
    )


def test_document_ocr_permission_is_required(api_context) -> None:
    client, _, _, _, principal, _ = api_context
    document = upload_pdf(client)
    principal["value"] = make_principal(
        "workspace_alpha",
        permissions={HumanControlPermission.DOCUMENT_VIEW.value},
    )
    response = client.post(
        "/api/workspaces/workspace_alpha/documents/"
        f"{document['id']}/ocr-runs",
        json={},
    )
    assert response.status_code == 403


def test_ocr_workspace_binding_and_isolation(api_context) -> None:
    client, _, _, _, principal, _ = api_context
    document = upload_pdf(client)
    base = (
        "/api/workspaces/workspace_alpha/documents/"
        f"{document['id']}/ocr-runs"
    )
    created = client.post(base, json={})
    run_id = created.json()["ocr_run"]["id"]

    mismatch = client.get(
        "/api/workspaces/workspace_beta/documents/"
        f"{document['id']}/ocr-runs"
    )
    assert mismatch.status_code == 403

    principal["value"] = make_principal(
        "workspace_beta",
        actor_id="operator_beta",
    )
    hidden = client.get(
        "/api/workspaces/workspace_beta/documents/"
        f"{document['id']}/ocr-runs/{run_id}"
    )
    assert hidden.status_code == 404


def test_ocr_request_validation_is_fail_closed(api_context) -> None:
    client, _, _, _, _, _ = api_context
    document = upload_pdf(client)
    base = (
        "/api/workspaces/workspace_alpha/documents/"
        f"{document['id']}/ocr-runs"
    )
    assert client.post(
        base,
        json={"page_numbers": [True]},
    ).status_code == 422
    assert client.post(
        base,
        json={"retention_days": 91},
    ).status_code == 422
    assert client.post(
        base,
        json={"unexpected": "value"},
    ).status_code == 422


def test_failed_runtime_returns_safe_gateway_error(
    api_context,
) -> None:
    client, session, registry, ocr, _, event_bus = api_context
    document = upload_pdf(client)
    ocr["value"] = DocumentOCRService(
        session=session,
        event_bus=event_bus,
        registry=registry,
        ocr=FailingOCR(),
    )
    response = client.post(
        "/api/workspaces/workspace_alpha/documents/"
        f"{document['id']}/ocr-runs",
        json={"page_numbers": [1]},
    )

    assert response.status_code == 502
    assert response.json()["ocr_run"]["status"] == "failed"
    assert response.json()["ocr_run"]["error_code"] == (
        "DOCUMENT_OCR_ENGINE_FAILED"
    )
    assert "secret OCR output" not in response.text


def test_retention_purge_api_removes_expired_pages(
    api_context,
) -> None:
    client, session, _, _, _, _ = api_context
    document = upload_pdf(client)
    base = (
        "/api/workspaces/workspace_alpha/documents/"
        f"{document['id']}"
    )
    created = client.post(
        f"{base}/ocr-runs",
        json={"retention_days": 1},
    )
    run_id = created.json()["ocr_run"]["id"]
    row = session.get(DocumentOCRRunModel, run_id)
    assert row is not None
    row.retention_expires_at = datetime.now(
        timezone.utc
    ) - timedelta(days=1)
    session.flush()

    purged = client.post(f"{base}/ocr-retention/purge")
    assert purged.status_code == 200, purged.text
    assert purged.json()["purged_runs"] == 1
    assert purged.json()["purged_pages"] == 3
    pages = client.get(
        f"{base}/ocr-runs/{run_id}/pages",
        params={"include_text": "true"},
    )
    assert pages.status_code == 200
    assert pages.json()["items"] == []


def test_document_delete_reports_ocr_cleanup(api_context) -> None:
    client, _, _, _, _, _ = api_context
    document = upload_pdf(client)
    base = (
        "/api/workspaces/workspace_alpha/documents/"
        f"{document['id']}"
    )
    created = client.post(
        f"{base}/ocr-runs",
        json={"page_numbers": [1, 2]},
    )
    assert created.status_code == 201

    deleted = client.delete(base)
    assert deleted.status_code == 200
    assert deleted.json()["derived_deleted"]["ocr_runs"] == 1
    assert deleted.json()["derived_deleted"]["ocr_pages"] == 2


def test_ocr_routes_are_in_openapi(api_context) -> None:
    client, _, _, _, _, _ = api_context
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    base = (
        "/api/workspaces/{workspace_id}/documents/"
        "{document_id}/ocr-runs"
    )
    assert base in paths
    assert f"{base}/{{run_id}}" in paths
    assert f"{base}/{{run_id}}/pages" in paths
    assert (
        "/api/workspaces/{workspace_id}/documents/"
        "{document_id}/ocr-retention/purge"
    ) in paths
