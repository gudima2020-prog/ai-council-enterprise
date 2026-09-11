from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.api.dependencies import get_container
from backend.control_center.governance_schemas import (
    HumanControlPermission,
)
from backend.control_center.security import (
    HumanControlPrincipal,
    get_request_principal,
)
from backend.core.events import EventBus
from backend.database import models as database_models  # noqa: F401
from backend.database.base import Base
from backend.database.models import WorkspaceModel
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.orchestration.tool_schemas import (
    ToolCreate,
    ToolPermissionCreate,
)
from backend.orchestration.tools import (
    ToolRegistryService,
    ToolRepository,
)
from backend.routers import tools


ALPHA = "workspace_alpha"
BETA = "workspace_beta"


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
    workspace_id: str | None,
    *,
    actor_id: str = "operator_alpha",
    permissions: set[str] | None = None,
) -> HumanControlPrincipal:
    effective = (
        {
            HumanControlPermission.VIEW.value,
            HumanControlPermission.MANAGE_POLICIES.value,
        }
        if permissions is None
        else permissions
    )

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


def tool_create(
    *,
    tool_key: str,
    workspace_id: str | None,
) -> ToolCreate:
    return ToolCreate(
        workspace_id=workspace_id,
        tool_key=tool_key,
        display_name=tool_key,
        kind="builtin",
        handler_ref="builtin.echo",
        risk_level="low",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )


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

    add_workspace(session, ALPHA)
    add_workspace(session, BETA)

    event_bus = EventBus()
    repository = ToolRepository(session)
    service = ToolRegistryService(repository, event_bus)

    alpha = repository.create(
        tool_create(
            tool_key="alpha.tool",
            workspace_id=ALPHA,
        )
    )
    beta = repository.create(
        tool_create(
            tool_key="beta.tool",
            workspace_id=BETA,
        )
    )
    global_tool = repository.create(
        tool_create(
            tool_key="global.tool",
            workspace_id=None,
        )
    )

    global_permission = repository.add_permission(
        alpha.id,
        ToolPermissionCreate(
            workspace_id=None,
            effect="allow",
            created_by="global_admin",
        ),
    )

    session.commit()

    ids = {
        "alpha": alpha.id,
        "beta": beta.id,
        "global": global_tool.id,
        "global_permission": global_permission.id,
    }

    principal = {
        "value": make_principal(ALPHA),
    }

    container = SimpleNamespace(
        human_control_auth_service=None,
    )

    app = FastAPI()
    app.include_router(tools.router, prefix="/api")

    async def principal_override() -> HumanControlPrincipal:
        return principal["value"]

    app.dependency_overrides[
        get_request_principal
    ] = principal_override

    app.dependency_overrides[
        tools.get_tool_service
    ] = lambda: service

    app.dependency_overrides[
        get_container
    ] = lambda: container

    with TestClient(app) as client:
        yield client, service, principal, ids

    session.close()
    engine.dispose()


def test_tool_mutation_requires_manage_policies(
    api_context,
) -> None:
    client, service, principal, ids = api_context

    principal["value"] = make_principal(
        ALPHA,
        permissions={HumanControlPermission.VIEW.value},
    )

    response = client.patch(
        f"/api/tools/{ids['alpha']}",
        json={
            "metadata": {
                "agent_policy_tool_id": "filesystem.read",
                "agent_policy_action_id": "filesystem.read",
                "agent_policy_runtime_operation": (
                    "metadata_validation"
                ),
            }
        },
    )

    assert response.status_code == 403

    stored = service.get(ids["alpha"])
    assert stored is not None
    assert stored["metadata"] == {}


def test_workspace_operator_cannot_mutate_foreign_or_global_tool(
    api_context,
) -> None:
    client, service, _, ids = api_context

    global_before = service.get(ids["global"])
    beta_before = service.get(ids["beta"])

    global_response = client.patch(
        f"/api/tools/{ids['global']}",
        json={"display_name": "tampered-global"},
    )
    beta_response = client.patch(
        f"/api/tools/{ids['beta']}",
        json={"display_name": "tampered-beta"},
    )

    assert global_response.status_code == 403
    assert beta_response.status_code == 403

    assert service.get(ids["global"])["display_name"] == (
        global_before["display_name"]
    )
    assert service.get(ids["beta"])["display_name"] == (
        beta_before["display_name"]
    )


def test_authorized_operator_creation_is_workspace_bound_and_mutable(
    api_context,
) -> None:
    client, service, _, _ = api_context

    created = client.post(
        "/api/tools",
        json={
            "tool_key": "authorized.read",
            "display_name": "Authorized read",
            "kind": "builtin",
            "handler_ref": "builtin.echo",
            "risk_level": "low",
            "allow_filesystem_read": True,
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
        },
    )

    assert created.status_code == 200, created.text
    body = created.json()

    assert body["workspace_id"] == ALPHA

    updated = client.patch(
        f"/api/tools/{body['id']}",
        json={
            "allow_filesystem_read": True,
            "metadata": {
                "agent_policy_tool_id": "filesystem.read",
                "agent_policy_action_id": "filesystem.read",
                "agent_policy_runtime_operation": (
                    "metadata_validation"
                ),
            },
        },
    )

    assert updated.status_code == 200, updated.text

    stored = service.get(body["id"])
    assert stored is not None
    assert stored["workspace_id"] == ALPHA
    assert stored["metadata"] == {
        "agent_policy_tool_id": "filesystem.read",
        "agent_policy_action_id": "filesystem.read",
        "agent_policy_runtime_operation": (
            "metadata_validation"
        ),
    }


def test_permission_creation_binds_workspace_and_actor(
    api_context,
) -> None:
    client, service, _, ids = api_context

    created = client.post(
        f"/api/tools/{ids['alpha']}/permissions",
        json={
            "effect": "allow",
        },
    )

    assert created.status_code == 200, created.text
    body = created.json()

    assert body["workspace_id"] == ALPHA
    assert body["created_by"] == "operator_alpha"

    permissions = service.list_permissions(ids["alpha"])
    stored = next(
        item
        for item in permissions
        if item["id"] == body["id"]
    )
    assert stored["workspace_id"] == ALPHA
    assert stored["created_by"] == "operator_alpha"


def test_permission_actor_spoofing_is_rejected(
    api_context,
) -> None:
    client, _, _, ids = api_context

    response = client.post(
        f"/api/tools/{ids['alpha']}/permissions",
        json={
            "effect": "allow",
            "created_by": "someone_else",
        },
    )

    assert response.status_code == 403


def test_workspace_operator_cannot_delete_global_permission(
    api_context,
) -> None:
    client, service, _, ids = api_context

    response = client.delete(
        f"/api/tools/{ids['alpha']}/permissions/"
        f"{ids['global_permission']}"
    )

    assert response.status_code == 403

    permission_ids = {
        item["id"]
        for item in service.list_permissions(ids["alpha"])
    }
    assert ids["global_permission"] in permission_ids
