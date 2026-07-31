from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.api.dependencies import (
    get_container,
    get_policy_approval_service,
)
from backend.control_center.security import (
    HumanControlPrincipal,
    get_request_principal,
)
from backend.core.events import EventBus
from backend.database.base import Base
from backend.database.models import WorkspaceModel
from backend.policy_approvals.service import PolicyApprovalService
from backend.routers import policy_approvals


def add_workspace(session: Session, workspace_id: str) -> None:
    session.add(
        WorkspaceModel(
            id=workspace_id,
            name=workspace_id,
            description="",
            workspace_type="general",
            status="active",
            metadata_json={},
        )
    )
    session.flush()


def make_principal(
    workspace_id: str,
    actor_id: str = "operator_alpha",
) -> HumanControlPrincipal:
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
        }
    )


def request_payload(
    *,
    subject_id: str = "request_001",
) -> dict:
    return {
        "scope": {
            "operation": "model_inference",
            "policy_version": "p2-011.1",
            "policy_fingerprint": "a" * 64,
            "subject_type": "gateway_route",
            "subject_id": subject_id,
            "subject_payload": {
                "provider": "svrtr",
                "model": "claude-sonnet",
                "request_fingerprint": "b" * 64,
            },
        },
        "reason_codes": ["APPROVAL_REQUIRED"],
        "requested_by": "operator_alpha",
        "request_note": "Exact scope approval required.",
        "ttl_seconds": 900,
        "metadata": {"source": "api_test"},
    }


@pytest.fixture
def api_context():
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

    service = PolicyApprovalService(
        session=session,
        event_bus=EventBus(),
    )
    principal = {
        "value": make_principal("workspace_alpha"),
    }
    container = SimpleNamespace(
        human_control_auth_service=None,
    )

    app = FastAPI()
    app.include_router(policy_approvals.router, prefix="/api")

    async def principal_override() -> HumanControlPrincipal:
        return principal["value"]

    app.dependency_overrides[
        get_request_principal
    ] = principal_override
    app.dependency_overrides[
        get_policy_approval_service
    ] = lambda: service
    app.dependency_overrides[get_container] = lambda: container

    with TestClient(app) as client:
        yield client, service, principal

    session.close()
    engine.dispose()


def test_request_list_get_and_idempotency(api_context) -> None:
    client, _, _ = api_context
    base = "/api/workspaces/workspace_alpha/policy-approvals"

    first = client.post(base, json=request_payload())
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["created"] is True
    approval_id = first_body["approval"]["id"]
    assert first_body["approval"]["status"] == "pending"
    assert "token_hash" not in first_body["approval"]

    second = client.post(base, json=request_payload())
    assert second.status_code == 200, second.text
    assert second.json()["created"] is False
    assert second.json()["approval"]["id"] == approval_id

    listed = client.get(base)
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [
        approval_id
    ]

    fetched = client.get(f"{base}/{approval_id}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["id"] == approval_id


def test_approve_returns_token_once_without_evidence_leak(
    api_context,
) -> None:
    client, _, _ = api_context
    base = "/api/workspaces/workspace_alpha/policy-approvals"
    created = client.post(
        base,
        json=request_payload(subject_id="request_approve"),
    )
    approval_id = created.json()["approval"]["id"]

    approved = client.post(
        f"{base}/{approval_id}/approve",
        json={
            "actor_id": "operator_alpha",
            "note": "Approved for the exact fingerprint.",
        },
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    token = body["token"]
    assert len(token) >= 32
    assert body["token_delivery"] == "one_time"
    assert body["approval"]["status"] == "approved"
    assert "token_hash" not in body["approval"]

    fetched = client.get(f"{base}/{approval_id}")
    assert fetched.status_code == 200
    assert "token" not in fetched.text
    assert "token_hash" not in fetched.text

    evidence = client.get(f"{base}/{approval_id}/evidence")
    assert evidence.status_code == 200, evidence.text
    assert token not in evidence.text
    assert "token_hash" not in evidence.text
    assert [
        item["event_type"]
        for item in evidence.json()["items"]
    ] == ["requested", "approved"]


def test_workspace_binding_and_storage_isolation(api_context) -> None:
    client, _, principal = api_context
    alpha_base = (
        "/api/workspaces/workspace_alpha/policy-approvals"
    )
    created = client.post(
        alpha_base,
        json=request_payload(subject_id="request_isolated"),
    )
    approval_id = created.json()["approval"]["id"]

    mismatch = client.get(
        "/api/workspaces/workspace_beta/policy-approvals"
    )
    assert mismatch.status_code == 403

    principal["value"] = make_principal(
        "workspace_beta",
        actor_id="operator_beta",
    )
    hidden = client.get(
        "/api/workspaces/workspace_beta/"
        f"policy-approvals/{approval_id}"
    )
    assert hidden.status_code == 404


def test_actor_binding_and_state_conflict(api_context) -> None:
    client, _, _ = api_context
    base = "/api/workspaces/workspace_alpha/policy-approvals"
    created = client.post(
        base,
        json=request_payload(subject_id="request_denied"),
    )
    approval_id = created.json()["approval"]["id"]

    mismatch = client.post(
        f"{base}/{approval_id}/deny",
        json={"actor_id": "another_operator"},
    )
    assert mismatch.status_code == 403

    denied = client.post(
        f"{base}/{approval_id}/deny",
        json={
            "actor_id": "operator_alpha",
            "note": "Risk was not accepted.",
        },
    )
    assert denied.status_code == 200, denied.text
    assert denied.json()["status"] == "denied"

    repeated = client.post(
        f"{base}/{approval_id}/approve",
        json={"actor_id": "operator_alpha"},
    )
    assert repeated.status_code == 409


def test_sensitive_scope_content_is_rejected(api_context) -> None:
    client, _, _ = api_context
    payload = request_payload(subject_id="request_sensitive")
    payload["scope"]["subject_payload"]["prompt"] = "raw prompt"

    response = client.post(
        "/api/workspaces/workspace_alpha/policy-approvals",
        json=payload,
    )

    assert response.status_code == 422
    assert "Sensitive field" in response.text


def test_policy_approval_routes_are_in_openapi(api_context) -> None:
    client, _, _ = api_context
    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    paths = schema.json()["paths"]
    base = "/api/workspaces/{workspace_id}/policy-approvals"

    assert base in paths
    assert f"{base}/{{approval_id}}/approve" in paths
    assert f"{base}/{{approval_id}}/deny" in paths
    assert f"{base}/{{approval_id}}/revoke" in paths
    assert f"{base}/{{approval_id}}/evidence" in paths
    assert f"{base}/reconcile-expired" in paths
