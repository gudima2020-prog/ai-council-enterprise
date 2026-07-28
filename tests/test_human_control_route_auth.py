from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.control_center.auth import HumanControlAuthService
from backend.control_center.auth_schemas import (
    AuthEnforcementMode,
    HumanControlApiTokenCreate,
    HumanControlAuthPolicyUpsert,
    HumanControlIdentityCreate,
    HumanControlLoginRequest,
)
from backend.control_center.schemas import HumanControlClaimRequest
from backend.control_center.security import (
    HumanControlActorMismatch,
    HumanControlAuthMiddleware,
    HumanControlPrincipal,
    bind_actor,
    require_permission,
)
from backend.core.events import EventBus
from backend.database import models as database_models
from backend.database.base import Base
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401


def make_scope():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    @contextmanager
    def scope():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    with scope() as session:
        session.add(database_models.WorkspaceModel(id="workspace_auth", name="Auth Test"))
        session.add(database_models.WorkspaceModel(id="workspace_other", name="Other"))
    return scope


async def configured_service(*, scopes: list[str] | None = None):
    scope = make_scope()
    service = HumanControlAuthService(event_bus=EventBus(), session_factory=scope)
    identity = await service.create_identity(HumanControlIdentityCreate(actor_id="alice", username="alice", display_name="Alice", password="correct-horse-battery-staple", created_by="bootstrap"))
    await service.upsert_policy(HumanControlAuthPolicyUpsert(enabled=True, enforcement_mode=AuthEnforcementMode.ENFORCE, actor_id="alice"))
    login = await service.login(HumanControlLoginRequest(username="alice", password="correct-horse-battery-staple"))
    api_token = None
    if scopes is not None:
        api_token = await service.create_api_token(HumanControlApiTokenCreate(identity_id=identity["id"], workspace_id="workspace_auth", name="test", scopes=scopes, created_by="alice"))
    return service, login, api_token


def make_app(service: HumanControlAuthService) -> FastAPI:
    app = FastAPI()
    app.state.container = SimpleNamespace(human_control_auth_service=service, human_control_governance_service=None)
    app.add_middleware(HumanControlAuthMiddleware)

    @app.get("/api/human-control/secure")
    async def secure(principal: HumanControlPrincipal = Depends(require_permission("human_control.view"))):
        return principal.as_dict()

    @app.get("/api/human-control/auth/login")
    async def public_login_marker():
        return {"public": True}

    return app


@pytest.mark.asyncio
async def test_enforce_mode_requires_bearer_for_protected_route() -> None:
    service, login, _ = await configured_service()
    client = TestClient(make_app(service))
    assert client.get("/api/human-control/secure").status_code == 401
    response = client.get("/api/human-control/secure", headers={"Authorization": f"Bearer {login['access_token']}"})
    assert response.status_code == 200
    assert response.json()["actor_id"] == "alice"


@pytest.mark.asyncio
async def test_public_auth_route_remains_available_in_enforce_mode() -> None:
    service, _, _ = await configured_service()
    response = TestClient(make_app(service)).get("/api/human-control/auth/login")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_api_token_must_have_required_scope() -> None:
    service, _, denied = await configured_service(scopes=["human_control.audit"])
    client = TestClient(make_app(service))
    response = client.get("/api/human-control/secure", headers={"Authorization": f"Bearer {denied['token']}"})
    assert response.status_code == 403

    allowed = await service.create_api_token(HumanControlApiTokenCreate(identity_id=denied["identity_id"], workspace_id="workspace_auth", name="allowed", scopes=["human_control.view"], created_by="alice"))
    response = client.get("/api/human-control/secure", headers={"Authorization": f"Bearer {allowed['token']}", "X-Workspace-ID": "workspace_auth"})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_workspace_bound_token_is_rejected_for_other_workspace() -> None:
    service, _, token = await configured_service(scopes=["human_control.view"])
    response = TestClient(make_app(service)).get("/api/human-control/secure", headers={"Authorization": f"Bearer {token['token']}", "X-Workspace-ID": "workspace_other"})
    assert response.status_code == 403


def test_actor_binding_rejects_spoofed_actor() -> None:
    principal = HumanControlPrincipal.from_payload({"identity_id": "id1", "actor_id": "alice", "username": "alice", "display_name": "Alice", "identity_type": "human", "workspace_id": None, "auth_method": "password", "scopes": [], "credential_id": "session1"})
    request = HumanControlClaimRequest(actor_id="mallory")
    with pytest.raises(HumanControlActorMismatch):
        bind_actor(request, principal)
    bound = bind_actor(HumanControlClaimRequest(actor_id="alice"), principal)
    assert bound.actor_id == "alice"


@pytest.mark.asyncio
async def test_policy_roundtrip_contains_route_security_fields() -> None:
    scope = make_scope()
    service = HumanControlAuthService(event_bus=EventBus(), session_factory=scope)
    policy = await service.upsert_policy(HumanControlAuthPolicyUpsert(enabled=True, enforcement_mode=AuthEnforcementMode.AUDIT, actor_id="owner", protected_path_prefixes=["/api/human-control", "/api/tasks"], public_paths=["/api/health*"], require_actor_binding=True, require_workspace_binding=True, reject_unscoped_api_tokens=True))
    assert policy["protected_path_prefixes"] == ["/api/human-control", "/api/tasks"]
    assert policy["public_paths"] == ["/api/health*"]
