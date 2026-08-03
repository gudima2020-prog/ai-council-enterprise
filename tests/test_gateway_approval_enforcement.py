from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.config import AppSettings
from backend.core.events import EventBus
from backend.database.base import Base
from backend.database.models import WorkspaceModel
from backend.gateway.approvals import GatewayApprovalCoordinator
from backend.gateway.policy import GatewayRoutePolicy
from backend.gateway.providers.base import ProviderAdapter
from backend.gateway.schemas import (
    GatewayError,
    GatewayRequest,
    GatewayResponse,
)
from backend.gateway.service import AIGateway
from backend.policy_approvals import PolicyApprovalStatus
from backend.policy_approvals.service import PolicyApprovalService
from backend.runtime_policy import (
    DataClassification,
    ProviderTrust,
)


class RecordingAdapter(ProviderAdapter):
    def __init__(
        self,
        *,
        name: str,
        response_factory: Callable[
            [GatewayRequest],
            GatewayResponse,
        ] | None = None,
        status_probe: Callable[[], None] | None = None,
    ) -> None:
        self.name = name
        self.calls: list[GatewayRequest] = []
        self._response_factory = response_factory
        self._status_probe = status_probe

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        if self._status_probe is not None:
            self._status_probe()
        self.calls.append(request)
        if self._response_factory is not None:
            return self._response_factory(request)
        return GatewayResponse(
            request_id=request.request_id,
            provider=request.provider,
            model=request.model,
            content="ok",
            status="success",
        )


def make_settings() -> AppSettings:
    return AppSettings(
        app_name="Gateway Approval Test",
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


@pytest.fixture
def environment():
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
        session.add(
            WorkspaceModel(
                id="workspace_alpha",
                name="Workspace Alpha",
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
    coordinator = GatewayApprovalCoordinator(
        session_scope_factory=session_scope_factory,
        event_bus=event_bus,
    )

    yield {
        "engine": engine,
        "session_scope": session_scope_factory,
        "event_bus": event_bus,
        "coordinator": coordinator,
    }

    engine.dispose()


def make_gateway(
    environment,
    *,
    providers: dict[str, ProviderAdapter],
    resolver=None,
    fallback_routes=None,
) -> AIGateway:
    if resolver is None:
        resolver = lambda workspace_id, provider: (
            DataClassification.CONFIDENTIAL,
            ProviderTrust.TRUSTED_EXTERNAL,
        )
    return AIGateway(
        settings=make_settings(),
        event_bus=environment["event_bus"],
        providers=providers,
        fallback_routes=fallback_routes,
        route_policy=GatewayRoutePolicy(
            event_bus=environment["event_bus"],
            resolver=resolver,
        ),
        approval_coordinator=environment["coordinator"],
    )


async def approve(environment, *, approval_id: str) -> str:
    with environment["session_scope"]() as session:
        grant = await PolicyApprovalService(
            session=session,
            event_bus=environment["event_bus"],
        ).approve(
            approval_id=approval_id,
            workspace_id="workspace_alpha",
            decided_by="operator_alpha",
            note="Approved for exact gateway route.",
        )
        return grant.token


@pytest.mark.asyncio
async def test_non_stream_requires_then_consumes_exact_approval(
    environment,
) -> None:
    adapter = RecordingAdapter(name="trusted")
    gateway = make_gateway(
        environment,
        providers={"trusted": adapter},
    )
    request_id = "gateway_enforcement_non_stream"

    pending = await gateway.ask(
        user_prompt="confidential prompt",
        system_prompt="system",
        workspace_id="workspace_alpha",
        request_id=request_id,
    )

    assert adapter.calls == []
    assert pending.error is not None
    assert pending.error.code == "POLICY_APPROVAL_REQUIRED"
    approval_id = pending.metadata[
        "policy_approval"
    ]["approval_id"]
    token = await approve(
        environment,
        approval_id=approval_id,
    )

    response = await gateway.ask(
        user_prompt="confidential prompt",
        system_prompt="system",
        workspace_id="workspace_alpha",
        request_id=request_id,
        approval_id=approval_id,
        approval_token=token,
    )

    assert response.status == "success"
    assert len(adapter.calls) == 1
    assert (
        response.metadata["policy_approval"]["status"]
        == "consumed"
    )
    assert token not in str(response.metadata)


@pytest.mark.asyncio
async def test_invalid_token_never_calls_provider(
    environment,
) -> None:
    adapter = RecordingAdapter(name="trusted")
    gateway = make_gateway(
        environment,
        providers={"trusted": adapter},
    )
    request_id = "gateway_enforcement_invalid_token"

    pending = await gateway.ask(
        user_prompt="confidential prompt",
        system_prompt="system",
        workspace_id="workspace_alpha",
        request_id=request_id,
    )
    approval_id = pending.metadata[
        "policy_approval"
    ]["approval_id"]
    await approve(environment, approval_id=approval_id)

    response = await gateway.ask(
        user_prompt="confidential prompt",
        system_prompt="system",
        workspace_id="workspace_alpha",
        request_id=request_id,
        approval_id=approval_id,
        approval_token="invalid-token",
    )

    assert adapter.calls == []
    assert response.error is not None
    assert (
        response.error.code
        == "POLICY_APPROVAL_TOKEN_INVALID"
    )


@pytest.mark.asyncio
async def test_stream_consumes_before_first_delta(
    environment,
) -> None:
    approval_ref: dict[str, str] = {}

    def assert_consumed() -> None:
        with environment["session_scope"]() as session:
            record = PolicyApprovalService(
                session=session,
                event_bus=environment["event_bus"],
            ).get(
                approval_id=approval_ref["id"],
                workspace_id="workspace_alpha",
            )
            assert record.status == PolicyApprovalStatus.CONSUMED

    adapter = RecordingAdapter(
        name="trusted",
        status_probe=assert_consumed,
    )
    gateway = make_gateway(
        environment,
        providers={"trusted": adapter},
    )
    request_id = "gateway_enforcement_stream"
    deltas: list[str] = []

    pending = await gateway.ask_stream(
        user_prompt="confidential prompt",
        system_prompt="system",
        on_delta=deltas.append,
        workspace_id="workspace_alpha",
        request_id=request_id,
    )

    assert adapter.calls == []
    assert deltas == []
    approval_id = pending.metadata[
        "policy_approval"
    ]["approval_id"]
    approval_ref["id"] = approval_id
    token = await approve(
        environment,
        approval_id=approval_id,
    )

    response = await gateway.ask_stream(
        user_prompt="confidential prompt",
        system_prompt="system",
        on_delta=deltas.append,
        workspace_id="workspace_alpha",
        request_id=request_id,
        approval_id=approval_id,
        approval_token=token,
    )

    assert response.status == "success"
    assert deltas == ["ok"]
    assert len(adapter.calls) == 1


@pytest.mark.asyncio
async def test_failover_route_cannot_bypass_approval(
    environment,
) -> None:
    def timeout_response(
        request: GatewayRequest,
    ) -> GatewayResponse:
        return GatewayResponse(
            request_id=request.request_id,
            provider=request.provider,
            model=request.model,
            content="",
            status="error",
            error=GatewayError(
                code="TIMEOUT",
                message="timeout",
                provider=request.provider,
                recoverable=True,
            ),
        )

    primary = RecordingAdapter(
        name="primary",
        response_factory=timeout_response,
    )
    fallback = RecordingAdapter(name="fallback")

    def resolver(
        workspace_id: str | None,
        provider: str,
    ):
        if provider == "primary":
            return (
                DataClassification.INTERNAL,
                ProviderTrust.EXTERNAL,
            )
        return (
            DataClassification.CONFIDENTIAL,
            ProviderTrust.TRUSTED_EXTERNAL,
        )

    gateway = make_gateway(
        environment,
        providers={
            "primary": primary,
            "fallback": fallback,
        },
        resolver=resolver,
        fallback_routes={
            "primary": [
                ("fallback", "fallback-model"),
            ],
        },
    )
    request_id = "gateway_enforcement_failover"

    pending = await gateway.ask(
        user_prompt="prompt",
        system_prompt="system",
        provider="primary",
        model="primary-model",
        workspace_id="workspace_alpha",
        request_id=request_id,
    )

    assert len(primary.calls) == 1
    assert fallback.calls == []
    assert pending.provider == "fallback"
    assert pending.error is not None
    assert pending.error.code == "POLICY_APPROVAL_REQUIRED"
    approval_id = pending.metadata[
        "policy_approval"
    ]["approval_id"]
    token = await approve(
        environment,
        approval_id=approval_id,
    )

    response = await gateway.ask(
        user_prompt="prompt",
        system_prompt="system",
        provider="primary",
        model="primary-model",
        workspace_id="workspace_alpha",
        request_id=request_id,
        approval_id=approval_id,
        approval_token=token,
    )

    assert response.status == "success"
    assert len(primary.calls) == 2
    assert len(fallback.calls) == 1
    assert response.provider == "fallback"
