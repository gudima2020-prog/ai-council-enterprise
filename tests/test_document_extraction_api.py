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
    get_document_extraction_service,
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
from backend.documents.extraction import (
    DocumentExtractionError,
)
from backend.documents.extraction_service import (
    DocumentExtractionService,
)
from backend.documents.service import (
    DocumentRegistryService,
)
from backend.documents.storage import (
    ManagedDocumentStorage,
)
from backend.routers import documents
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


def make_principal(
    workspace_id: str,
    *,
    actor_id: str = "operator_alpha",
    permissions: set[str] | None = None,
) -> HumanControlPrincipal:
    effective = permissions or {
        HumanControlPermission.DOCUMENT_VIEW.value,
        HumanControlPermission.DOCUMENT_UPLOAD.value,
        HumanControlPermission.DOCUMENT_DELETE.value,
        HumanControlPermission.DOCUMENT_AUDIT.value,
        HumanControlPermission.DOCUMENT_EXTRACT.value,
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


class FailingExtractor:
    PARSER_VERSION = "p3-001.3a-v1"

    def extract(self, **kwargs):
        raise DocumentExtractionError(
            "DOCUMENT_EXTRACTION_TEST_FAILURE",
            "Synthetic extraction failure.",
            details={"page_number": 3},
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
    extraction = {
        "value": DocumentExtractionService(
            session=session,
            event_bus=event_bus,
            registry=registry,
        )
    }
    principal = {
        "value": make_principal(
            "workspace_alpha"
        )
    }

    class PolicyServiceStub:
        def get_effective_policy(
            self,
            workspace_id: str,
        ):
            return SimpleNamespace(
                data_classification=(
                    DataClassification.CONFIDENTIAL
                )
            )

    container = SimpleNamespace(
        human_control_auth_service=None,
    )
    app = FastAPI()
    app.include_router(
        documents.router,
        prefix="/api",
    )

    async def principal_override():
        return principal["value"]

    app.dependency_overrides[
        get_request_principal
    ] = principal_override
    app.dependency_overrides[
        get_document_registry_service
    ] = lambda: registry
    app.dependency_overrides[
        get_document_extraction_service
    ] = lambda: extraction["value"]
    app.dependency_overrides[
        get_workspace_policy_service
    ] = PolicyServiceStub
    app.dependency_overrides[
        get_container
    ] = lambda: container

    with TestClient(app) as client:
        yield (
            client,
            session,
            registry,
            extraction,
            principal,
            event_bus,
        )

    session.close()
    engine.dispose()


def upload_txt(
    client: TestClient,
    *,
    content: bytes = b"alpha beta gamma",
):
    response = client.post(
        "/api/workspaces/workspace_alpha/documents",
        files={
            "file": (
                "notes.txt",
                content,
                "text/plain",
            )
        },
        data={
            "metadata_json": json.dumps(
                {"source": "api"}
            )
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["document"]


def test_extract_list_get_units_and_chunks(
    api_context,
) -> None:
    client, _, _, _, _, _ = api_context
    document = upload_txt(client)
    base = (
        "/api/workspaces/workspace_alpha/"
        f"documents/{document['id']}/extractions"
    )

    created = client.post(base)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["created"] is True
    assert body["reused"] is False
    assert body["extraction"]["status"] == "completed"
    run_id = body["extraction"]["id"]

    listed = client.get(base)
    assert listed.status_code == 200, listed.text
    assert [
        item["id"]
        for item in listed.json()["items"]
    ] == [run_id]

    fetched = client.get(f"{base}/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == run_id

    units = client.get(
        f"{base}/{run_id}/units"
    )
    assert units.status_code == 200
    assert units.json()["items"][0]["text"] == (
        "alpha beta gamma"
    )

    chunks = client.get(
        f"{base}/{run_id}/chunks"
    )
    assert chunks.status_code == 200
    assert chunks.json()["items"]


def test_repeat_extract_reuses_completed_run(
    api_context,
) -> None:
    client, _, _, _, _, _ = api_context
    document = upload_txt(client)
    base = (
        "/api/workspaces/workspace_alpha/"
        f"documents/{document['id']}/extractions"
    )

    first = client.post(base)
    second = client.post(base)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["reused"] is True
    assert (
        second.json()["extraction"]["id"]
        == first.json()["extraction"]["id"]
    )


def test_extract_permission_is_required(
    api_context,
) -> None:
    client, _, _, _, principal, _ = api_context
    document = upload_txt(client)
    principal["value"] = make_principal(
        "workspace_alpha",
        permissions={
            HumanControlPermission.DOCUMENT_VIEW.value
        },
    )

    response = client.post(
        "/api/workspaces/workspace_alpha/"
        f"documents/{document['id']}/extractions"
    )

    assert response.status_code == 403


def test_workspace_binding_and_isolation(
    api_context,
) -> None:
    client, _, _, _, principal, _ = api_context
    document = upload_txt(client)
    base = (
        "/api/workspaces/workspace_alpha/"
        f"documents/{document['id']}/extractions"
    )
    created = client.post(base)
    run_id = created.json()["extraction"]["id"]

    mismatch = client.get(
        "/api/workspaces/workspace_beta/"
        f"documents/{document['id']}/extractions"
    )
    assert mismatch.status_code == 403

    principal["value"] = make_principal(
        "workspace_beta",
        actor_id="operator_beta",
    )
    hidden = client.get(
        "/api/workspaces/workspace_beta/"
        f"documents/{document['id']}/"
        f"extractions/{run_id}"
    )
    assert hidden.status_code == 404


def test_failed_extraction_returns_422_and_persists(
    api_context,
) -> None:
    (
        client,
        session,
        registry,
        extraction,
        _,
        event_bus,
    ) = api_context
    document = upload_txt(
        client,
        content=b"private body",
    )
    extraction["value"] = DocumentExtractionService(
        session=session,
        event_bus=event_bus,
        registry=registry,
        extractor=FailingExtractor(),
    )
    base = (
        "/api/workspaces/workspace_alpha/"
        f"documents/{document['id']}/extractions"
    )

    failed = client.post(base)
    assert failed.status_code == 422, failed.text
    body = failed.json()
    assert body["extraction"]["status"] == "failed"
    assert body["extraction"]["error_code"] == (
        "DOCUMENT_EXTRACTION_TEST_FAILURE"
    )
    assert "private body" not in failed.text
    run_id = body["extraction"]["id"]

    persisted = client.get(f"{base}/{run_id}")
    assert persisted.status_code == 200
    assert persisted.json()["status"] == "failed"


def test_document_delete_reports_derived_cleanup(
    api_context,
) -> None:
    client, _, _, _, _, _ = api_context
    document = upload_txt(client)
    base = (
        "/api/workspaces/workspace_alpha/"
        f"documents/{document['id']}"
    )
    extracted = client.post(
        f"{base}/extractions"
    )
    assert extracted.status_code == 201

    deleted = client.delete(base)

    assert deleted.status_code == 200
    assert deleted.json()["derived_deleted"][
        "runs"
    ] == 1


def test_extraction_routes_are_in_openapi(
    api_context,
) -> None:
    client, _, _, _, _, _ = api_context
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    base = (
        "/api/workspaces/{workspace_id}/documents/"
        "{document_id}/extractions"
    )
    assert base in paths
    assert f"{base}/{{run_id}}" in paths
    assert f"{base}/{{run_id}}/units" in paths
    assert f"{base}/{{run_id}}/chunks" in paths
