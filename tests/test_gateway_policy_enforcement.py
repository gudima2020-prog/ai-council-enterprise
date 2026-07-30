from __future__ import annotations

from collections.abc import Callable

import pytest

from backend.core.config import AppSettings
from backend.core.events import EventBus
from backend.gateway.policy import GatewayRoutePolicy
from backend.gateway.providers.base import ProviderAdapter
from backend.gateway.schemas import (
    GatewayError,
    GatewayRequest,
    GatewayResponse,
)
from backend.gateway.service import AIGateway
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
    ) -> None:
        self.name = name
        self.calls: list[GatewayRequest] = []
        self._response_factory = response_factory

    def complete(
        self,
        request: GatewayRequest,
    ) -> GatewayResponse:
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
        app_name="Gateway Policy Test",
        app_version="0",
        environment="test",
        debug=True,
        host="127.0.0.1",
        port=8000,
        openrouter_api_key="",
        default_provider="primary",
        default_model="primary-model",
        max_tokens=1000,
        temperature=0.4,
        request_timeout_seconds=60,
    )


def make_gateway(
    *,
    providers: dict[str, ProviderAdapter],
    resolver,
    fallback_routes: dict[
        str,
        list[tuple[str, str]],
    ] | None = None,
) -> AIGateway:
    event_bus = EventBus()

    return AIGateway(
        settings=make_settings(),
        event_bus=event_bus,
        providers=providers,
        fallback_routes=fallback_routes,
        route_policy=GatewayRoutePolicy(
            event_bus=event_bus,
            resolver=resolver,
        ),
    )


@pytest.mark.asyncio
async def test_denied_route_never_calls_provider_adapter() -> None:
    adapter = RecordingAdapter(name="primary")
    gateway = make_gateway(
        providers={"primary": adapter},
        resolver=lambda workspace_id, provider: (
            DataClassification.CONFIDENTIAL,
            ProviderTrust.EXTERNAL,
        ),
    )

    response = await gateway.ask(
        user_prompt="sensitive prompt",
        system_prompt="system",
        workspace_id="workspace-1",
    )

    assert adapter.calls == []
    assert response.status == "error"
    assert response.error is not None
    assert response.error.code == "POLICY_DENIED"
    assert (
        response.metadata["runtime_policy"]["action"]
        == "deny"
    )


@pytest.mark.asyncio
async def test_allowed_route_receives_policy_metadata() -> None:
    adapter = RecordingAdapter(name="primary")
    gateway = make_gateway(
        providers={"primary": adapter},
        resolver=lambda workspace_id, provider: (
            DataClassification.INTERNAL,
            ProviderTrust.EXTERNAL,
        ),
    )

    response = await gateway.ask(
        user_prompt="ordinary prompt",
        system_prompt="system",
        workspace_id="workspace-1",
    )

    assert response.status == "success"
    assert len(adapter.calls) == 1

    request_policy = adapter.calls[0].metadata[
        "runtime_policy"
    ]
    response_policy = response.metadata[
        "runtime_policy"
    ]

    assert request_policy["action"] == "allow"
    assert response_policy == request_policy
    assert len(request_policy["fingerprint"]) == 64


@pytest.mark.asyncio
async def test_failover_route_is_evaluated_before_adapter_call() -> None:
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
        if provider == "fallback":
            return (
                DataClassification.RESTRICTED,
                ProviderTrust.EXTERNAL,
            )

        return (
            DataClassification.INTERNAL,
            ProviderTrust.EXTERNAL,
        )

    gateway = make_gateway(
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

    response = await gateway.ask(
        user_prompt="prompt",
        system_prompt="system",
        workspace_id="workspace-1",
    )

    assert len(primary.calls) == 1
    assert fallback.calls == []
    assert response.status == "error"
    assert response.error is not None
    assert response.error.code == "POLICY_DENIED"
    assert response.provider == "fallback"


@pytest.mark.asyncio
async def test_stream_denial_emits_no_content_and_calls_no_adapter() -> None:
    adapter = RecordingAdapter(name="primary")
    gateway = make_gateway(
        providers={"primary": adapter},
        resolver=lambda workspace_id, provider: (
            DataClassification.CONFIDENTIAL,
            ProviderTrust.TRUSTED_EXTERNAL,
        ),
    )
    deltas: list[str] = []

    response = await gateway.ask_stream(
        user_prompt="sensitive prompt",
        system_prompt="system",
        on_delta=deltas.append,
        workspace_id="workspace-1",
    )

    assert adapter.calls == []
    assert deltas == []
    assert response.status == "error"
    assert response.error is not None
    assert (
        response.error.code
        == "POLICY_APPROVAL_REQUIRED"
    )
