from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.agent_governance import (
    AgentProfileService,
    agent_tool_capability_catalog,
    built_in_agent_profiles,
)
from backend.api.dependencies import (
    get_agent_profile_service,
    get_container,
)
from backend.control_center.governance_schemas import HumanControlPermission
from backend.control_center.security import (
    HumanControlPrincipal,
    get_request_principal,
)
from backend.database.base import Base
from backend.database.models import WorkspaceModel
from backend.routers import agent_profiles


def add_workspace(session: Session, workspace_id: str) -> None:
    session.add(
        WorkspaceModel(
            id=workspace_id,
            name=workspace_id,
            description="",
            workspace_type="development",
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
        HumanControlPermission.VIEW.value,
        HumanControlPermission.MANAGE_POLICIES.value,
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


def custom_payload(
    *,
    profile_id: str = "custom-safe",
    version: str = "1.0.0",
) -> dict:
    catalog = agent_tool_capability_catalog()
    allowed_tools = ["filesystem.read", "tests.run"]
    return {
        "profile_id": profile_id,
        "version": version,
        "audit_version": "2026.08.07",
        "allowed_tools": allowed_tools,
        "tool_capabilities": {
            tool_id: [
                capability.value
                for capability in catalog[tool_id]
            ]
            for tool_id in allowed_tools
        },
        "denied_actions": ["git.force_push"],
        "mandatory_checks": [
            "policy.runtime",
            "review.independent",
        ],
        "approval_conditions": ["code_execution"],
        "external_domains": [],
        "allowed_classifications": ["public", "internal"],
        "primary_model": "workspace.primary",
        "reviewer_model": "workspace.reviewer",
        "network_access": "denied",
        "filesystem_access": "read_only",
    }


@pytest.fixture
def api_context():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    session = Session(engine)
    Base.metadata.create_all(engine)
    add_workspace(session, "workspace_alpha")
    add_workspace(session, "workspace_beta")

    service = AgentProfileService(session)
    principal = {"value": make_principal("workspace_alpha")}
    container = SimpleNamespace(human_control_auth_service=None)

    app = FastAPI()
    app.include_router(agent_profiles.router, prefix="/api")

    async def principal_override() -> HumanControlPrincipal:
        return principal["value"]

    app.dependency_overrides[
        get_request_principal
    ] = principal_override
    app.dependency_overrides[
        get_agent_profile_service
    ] = lambda: service
    app.dependency_overrides[get_container] = lambda: container

    with TestClient(app) as client:
        yield client, service, principal

    session.close()
    engine.dispose()


def test_create_list_get_and_idempotent_custom_profile(api_context) -> None:
    client, _, _ = api_context
    base = "/api/workspaces/workspace_alpha/agent-profiles"
    payload = custom_payload()

    created = client.post(f"{base}/custom", json=payload)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["created"] is True
    fingerprint = body["profile"]["profile_fingerprint"]
    assert len(fingerprint) == 64
    assert body["profile"]["created_by"] == "operator_alpha"

    repeated = client.post(f"{base}/custom", json=payload)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["created"] is False
    assert (
        repeated.json()["profile"]["profile_fingerprint"]
        == fingerprint
    )

    listed = client.get(f"{base}/custom")
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1

    fetched = client.get(f"{base}/custom/custom-safe/1.0.0")
    assert fetched.status_code == 200
    assert fetched.json()["profile_fingerprint"] == fingerprint


def test_built_ins_are_listed_but_cannot_be_created_as_custom(
    api_context,
) -> None:
    client, _, _ = api_context
    base = "/api/workspaces/workspace_alpha/agent-profiles"

    built_ins = client.get(f"{base}/built-ins")
    assert built_ins.status_code == 200
    assert len(built_ins.json()["items"]) == 6

    built_in = built_in_agent_profiles()[0]
    payload = built_in.to_dict()
    blocked = client.post(f"{base}/custom", json=payload)
    assert blocked.status_code == 409


def test_mutation_requires_manage_policies_permission(api_context) -> None:
    client, _, principal = api_context
    principal["value"] = make_principal(
        "workspace_alpha",
        permissions={HumanControlPermission.VIEW.value},
    )

    response = client.post(
        "/api/workspaces/workspace_alpha/agent-profiles/custom",
        json=custom_payload(),
    )
    assert response.status_code == 403


def test_mutation_requires_authenticated_actor_even_in_legacy_mode(
    api_context,
) -> None:
    client, _, principal = api_context
    principal["value"] = HumanControlPrincipal.anonymous(
        "workspace_alpha"
    )

    response = client.post(
        "/api/workspaces/workspace_alpha/agent-profiles/custom",
        json=custom_payload(),
    )
    assert response.status_code == 401


def test_workspace_binding_rejects_cross_workspace_path(api_context) -> None:
    client, _, _ = api_context

    response = client.get(
        "/api/workspaces/workspace_beta/agent-profiles/custom"
    )
    assert response.status_code == 403


def test_cross_workspace_custom_profile_is_hidden(api_context) -> None:
    client, _, principal = api_context
    alpha = "/api/workspaces/workspace_alpha/agent-profiles"
    created = client.post(
        f"{alpha}/custom",
        json=custom_payload(),
    )
    assert created.status_code == 201

    principal["value"] = make_principal(
        "workspace_beta",
        actor_id="operator_beta",
    )
    hidden = client.get(
        "/api/workspaces/workspace_beta/agent-profiles/"
        "custom/custom-safe/1.0.0"
    )
    assert hidden.status_code == 404


def test_active_selection_binds_exact_fingerprint(api_context) -> None:
    client, _, _ = api_context
    base = "/api/workspaces/workspace_alpha/agent-profiles"
    created = client.post(
        f"{base}/custom",
        json=custom_payload(),
    )
    fingerprint = created.json()["profile"]["profile_fingerprint"]

    selected = client.put(
        f"{base}/active",
        json={
            "profile_id": "custom-safe",
            "version": "1.0.0",
            "expected_fingerprint": fingerprint,
        },
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["source"] == "custom"
    assert selected.json()["profile_fingerprint"] == fingerprint

    active = client.get(f"{base}/active")
    assert active.status_code == 200
    assert active.json()["profile_id"] == "custom-safe"


def test_active_selection_rejects_modified_fingerprint(api_context) -> None:
    client, _, _ = api_context
    base = "/api/workspaces/workspace_alpha/agent-profiles"
    created = client.post(
        f"{base}/custom",
        json=custom_payload(),
    )
    assert created.status_code == 201

    rejected = client.put(
        f"{base}/active",
        json={
            "profile_id": "custom-safe",
            "version": "1.0.0",
            "expected_fingerprint": "0" * 64,
        },
    )
    assert rejected.status_code == 409


def test_unknown_or_underdeclared_tool_binding_is_rejected(
    api_context,
) -> None:
    client, _, _ = api_context
    payload = custom_payload()
    payload["allowed_tools"] = ["git.push"]
    payload["tool_capabilities"] = {"git.push": []}

    response = client.post(
        "/api/workspaces/workspace_alpha/agent-profiles/custom",
        json=payload,
    )
    assert response.status_code == 400


def test_agent_profile_routes_are_in_openapi(api_context) -> None:
    client, _, _ = api_context
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    base = "/api/workspaces/{workspace_id}/agent-profiles"
    assert f"{base}/built-ins" in paths
    assert f"{base}/custom" in paths
    assert f"{base}/custom/{{profile_id}}/{{version}}" in paths
    assert f"{base}/active" in paths
