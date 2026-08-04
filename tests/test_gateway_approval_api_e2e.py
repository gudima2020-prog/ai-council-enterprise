from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.dependencies import (
    get_ai_gateway,
    get_container,
    get_policy_approval_service,
)
from backend.control_center.security import (
    HumanControlPrincipal,
    get_request_principal,
)
from backend.core.config import AppSettings
from backend.core.events import EventBus
from backend.database.base import Base
from backend.database.models import WorkspaceModel
from backend.gateway.approvals import GatewayApprovalCoordinator
from backend.gateway.policy import GatewayRoutePolicy
from backend.gateway.providers.base import ProviderAdapter
from backend.gateway.schemas import GatewayRequest, GatewayResponse
from backend.gateway.service import AIGateway
from backend.policy_approvals.service import PolicyApprovalService
from backend.routers import gateway, policy_approvals
from backend.runtime_policy import DataClassification, ProviderTrust


class RecordingAdapter(ProviderAdapter):
    def __init__(self) -> None:
        self.name = "trusted"
        self.calls: list[GatewayRequest] = []

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        self.calls.append(request)
        return GatewayResponse(
            request_id=request.request_id,
            provider=request.provider,
            model=request.model,
            content="approved response",
            status="success",
        )


def make_settings() -> AppSettings:
    return AppSettings(
        app_name="Gateway Approval API E2E",
        app_version="0",
        environment="test",
        debug=True,
        host="127.0.0.1",
        port=8000,
        openrouter_api_key="",
        default_provider="trusted",
        default_model="model-a",
        max_tokens=1000,
        temperature=0.4,
        request_timeout_seconds=60,
    )


def make_principal(workspace_id: str, actor_id: str) -> HumanControlPrincipal:
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


@pytest.fixture
def api_environment():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )
    with session_factory() as session:
        for workspace_id in ("workspace_alpha", "workspace_beta"):
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
        session.commit()

    @contextmanager
    def session_scope_factory():
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    event_bus = EventBus()
    adapter = RecordingAdapter()
    gateway_service = AIGateway(
        settings=make_settings(),
        event_bus=event_bus,
        providers={"trusted": adapter},
        route_policy=GatewayRoutePolicy(
            event_bus=event_bus,
            resolver=lambda workspace_id, provider: (
                DataClassification.CONFIDENTIAL,
                ProviderTrust.TRUSTED_EXTERNAL,
            ),
        ),
        approval_coordinator=GatewayApprovalCoordinator(
            session_scope_factory=session_scope_factory,
            event_bus=event_bus,
        ),
    )
    principal = {
        "value": make_principal("workspace_alpha", "operator_alpha")
    }
    container = SimpleNamespace(
        human_control_auth_service=None,
        event_bus=event_bus,
        settings=make_settings(),
        secret_manager_service=None,
    )
    app = FastAPI()
    app.include_router(gateway.router, prefix="/api")
    app.include_router(policy_approvals.router, prefix="/api")

    async def principal_override():
        return principal["value"]

    def policy_service_override():
        with session_scope_factory() as session:
            yield PolicyApprovalService(session=session, event_bus=event_bus)

    app.dependency_overrides[get_request_principal] = principal_override
    app.dependency_overrides[get_ai_gateway] = lambda: gateway_service
    app.dependency_overrides[
        get_policy_approval_service
    ] = policy_service_override
    app.dependency_overrides[get_container] = lambda: container

    with TestClient(app) as client:
        yield {"client": client, "adapter": adapter, "principal": principal}
    engine.dispose()


def inference_payload(*, request_id: str, prompt: str = "confidential prompt") -> dict:
    return {
        "user_prompt": prompt,
        "mode": "universal",
        "provider": "trusted",
        "model": "model-a",
        "request_id": request_id,
    }


def approve(
    client: TestClient,
    *,
    approval_id: str,
    workspace_id: str = "workspace_alpha",
    actor_id: str = "operator_alpha",
) -> str:
    response = client.post(
        f"/api/workspaces/{workspace_id}/policy-approvals/{approval_id}/approve",
        json={"actor_id": actor_id, "note": "Approved for exact API request."},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def test_api_to_gateway_to_provider_approval_flow(api_environment) -> None:
    client = api_environment["client"]
    adapter = api_environment["adapter"]
    endpoint = "/api/workspaces/workspace_alpha/gateway/inference"
    payload = inference_payload(request_id="gateway_api_e2e")

    pending = client.post(endpoint, json=payload)
    assert pending.status_code == 202, pending.text
    body = pending.json()
    assert body["error"]["code"] == "POLICY_APPROVAL_REQUIRED"
    assert adapter.calls == []
    approval_id = body["metadata"]["policy_approval"]["approval_id"]
    token = approve(client, approval_id=approval_id)

    resumed = client.post(
        endpoint,
        json={**payload, "approval_id": approval_id, "approval_token": token},
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "success"
    assert resumed.json()["metadata"]["policy_approval"]["status"] == "consumed"
    assert len(adapter.calls) == 1
    assert token not in resumed.text

    fetched = client.get(
        f"/api/workspaces/workspace_alpha/policy-approvals/{approval_id}"
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["status"] == "consumed"
    assert token not in fetched.text

    evidence = client.get(
        f"/api/workspaces/workspace_alpha/policy-approvals/{approval_id}/evidence"
    )
    assert evidence.status_code == 200, evidence.text
    assert token not in evidence.text
    assert payload["user_prompt"] not in evidence.text
    assert [item["event_type"] for item in evidence.json()["items"]] == [
        "requested",
        "approved",
        "consumed",
    ]


def test_reused_token_is_fail_closed(api_environment) -> None:
    client = api_environment["client"]
    adapter = api_environment["adapter"]
    endpoint = "/api/workspaces/workspace_alpha/gateway/inference"
    payload = inference_payload(request_id="gateway_api_reuse")
    pending = client.post(endpoint, json=payload)
    approval_id = pending.json()["metadata"]["policy_approval"]["approval_id"]
    token = approve(client, approval_id=approval_id)
    authorized = {**payload, "approval_id": approval_id, "approval_token": token}

    first = client.post(endpoint, json=authorized)
    assert first.status_code == 200, first.text
    assert len(adapter.calls) == 1
    second = client.post(endpoint, json=authorized)
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "POLICY_APPROVAL_INVALID_STATE"
    assert len(adapter.calls) == 1
    assert token not in second.text


def test_scope_mismatch_never_calls_provider(api_environment) -> None:
    client = api_environment["client"]
    adapter = api_environment["adapter"]
    endpoint = "/api/workspaces/workspace_alpha/gateway/inference"
    payload = inference_payload(request_id="gateway_api_scope")
    pending = client.post(endpoint, json=payload)
    approval_id = pending.json()["metadata"]["policy_approval"]["approval_id"]
    token = approve(client, approval_id=approval_id)

    mismatched = client.post(
        endpoint,
        json={
            **payload,
            "user_prompt": "changed confidential prompt",
            "approval_id": approval_id,
            "approval_token": token,
        },
    )
    assert mismatched.status_code == 403, mismatched.text
    assert mismatched.json()["error"]["code"] == "POLICY_APPROVAL_SCOPE_MISMATCH"
    assert adapter.calls == []
    assert token not in mismatched.text


def test_workspace_binding_and_cross_workspace_token(api_environment) -> None:
    client = api_environment["client"]
    adapter = api_environment["adapter"]
    principal = api_environment["principal"]
    alpha_endpoint = "/api/workspaces/workspace_alpha/gateway/inference"
    payload = inference_payload(request_id="gateway_api_workspace")
    pending = client.post(alpha_endpoint, json=payload)
    approval_id = pending.json()["metadata"]["policy_approval"]["approval_id"]
    token = approve(client, approval_id=approval_id)

    mismatch = client.post(
        "/api/workspaces/workspace_beta/gateway/inference",
        json={**payload, "approval_id": approval_id, "approval_token": token},
    )
    assert mismatch.status_code == 403
    assert adapter.calls == []

    principal["value"] = make_principal("workspace_beta", "operator_beta")
    hidden = client.post(
        "/api/workspaces/workspace_beta/gateway/inference",
        json={**payload, "approval_id": approval_id, "approval_token": token},
    )
    assert hidden.status_code == 404, hidden.text
    assert hidden.json()["error"]["code"] == "POLICY_APPROVAL_NOT_FOUND"
    assert adapter.calls == []
    assert token not in hidden.text


def test_partial_approval_credentials_are_rejected(api_environment) -> None:
    client = api_environment["client"]
    adapter = api_environment["adapter"]
    response = client.post(
        "/api/workspaces/workspace_alpha/gateway/inference",
        json={
            **inference_payload(request_id="gateway_api_partial"),
            "approval_id": "approval_without_token",
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "POLICY_APPROVAL_CREDENTIALS_INCOMPLETE"
    assert adapter.calls == []


def test_gateway_inference_route_is_in_openapi(api_environment) -> None:
    schema = api_environment["client"].get("/openapi.json")
    assert schema.status_code == 200
    assert (
        "/api/workspaces/{workspace_id}/gateway/inference"
        in schema.json()["paths"]
    )
