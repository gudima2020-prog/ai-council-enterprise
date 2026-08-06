from __future__ import annotations

import pytest

from backend.core.config import AppSettings
from backend.core.events import EventBus
from backend.gateway.approvals import gateway_request_fingerprint
from backend.gateway.policy import GatewayRoutePolicy
from backend.gateway.providers.base import ProviderAdapter
from backend.gateway.schemas import GatewayRequest, GatewayResponse
from backend.gateway.service import AIGateway
from backend.runtime_policy import DataClassification, ProviderTrust


class RecordingAdapter(ProviderAdapter):
    name = "provider"

    def __init__(self) -> None:
        self.requests: list[GatewayRequest] = []

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        self.requests.append(request)
        return GatewayResponse(
            request_id=request.request_id,
            provider=request.provider,
            model=request.model,
            content='{"answer":"ok","citation_ids":["D1"]}',
            status="success",
        )


def make_gateway(
    *,
    workspace_classification: DataClassification,
    provider_trust: ProviderTrust,
) -> tuple[AIGateway, RecordingAdapter]:
    event_bus = EventBus()
    adapter = RecordingAdapter()
    settings = AppSettings(
        app_name="test",
        app_version="0",
        environment="test",
        debug=True,
        host="127.0.0.1",
        port=8000,
        openrouter_api_key="",
        default_provider="provider",
        default_model="model",
        max_tokens=1000,
        temperature=0.4,
        request_timeout_seconds=60,
    )
    gateway = AIGateway(
        settings=settings,
        event_bus=event_bus,
        providers={"provider": adapter},
        route_policy=GatewayRoutePolicy(
            event_bus=event_bus,
            resolver=lambda workspace_id, provider: (
                workspace_classification,
                provider_trust,
            ),
        ),
    )
    return gateway, adapter


@pytest.mark.asyncio
async def test_document_classification_override_can_only_raise_policy() -> None:
    gateway, adapter = make_gateway(
        workspace_classification=DataClassification.INTERNAL,
        provider_trust=ProviderTrust.EXTERNAL,
    )

    response = await gateway.ask(
        user_prompt="document data",
        system_prompt="system",
        workspace_id="workspace_alpha",
        data_classification=DataClassification.CONFIDENTIAL.value,
    )

    assert response.status == "error"
    assert response.error is not None
    assert response.error.code == "POLICY_DENIED"
    assert adapter.requests == []
    assert response.metadata["runtime_policy"]["data_classification"] == (
        "confidential"
    )


@pytest.mark.asyncio
async def test_document_classification_cannot_downgrade_workspace_policy() -> None:
    gateway, adapter = make_gateway(
        workspace_classification=DataClassification.CONFIDENTIAL,
        provider_trust=ProviderTrust.EXTERNAL,
    )

    response = await gateway.ask(
        user_prompt="document data",
        system_prompt="system",
        workspace_id="workspace_alpha",
        data_classification=DataClassification.INTERNAL.value,
    )

    assert response.status == "error"
    assert adapter.requests == []
    assert response.metadata["runtime_policy"]["data_classification"] == (
        "confidential"
    )


@pytest.mark.asyncio
async def test_gateway_uses_exact_document_generation_limits() -> None:
    gateway, adapter = make_gateway(
        workspace_classification=DataClassification.INTERNAL,
        provider_trust=ProviderTrust.EXTERNAL,
    )

    response = await gateway.ask(
        user_prompt="document data",
        system_prompt="system",
        workspace_id="workspace_alpha",
        data_classification=DataClassification.INTERNAL.value,
        temperature=0.0,
        max_tokens=321,
    )

    assert response.status == "success"
    assert adapter.requests[0].temperature == 0.0
    assert adapter.requests[0].max_tokens == 321
    assert adapter.requests[0].data_classification == "internal"


def test_gateway_approval_fingerprint_binds_document_classification() -> None:
    base = GatewayRequest(
        messages=[],
        model="model",
        provider="provider",
        workspace_id="workspace_alpha",
        data_classification="internal",
    )
    sensitive = GatewayRequest(
        messages=[],
        model="model",
        provider="provider",
        workspace_id="workspace_alpha",
        data_classification="confidential",
        request_id=base.request_id,
    )

    assert gateway_request_fingerprint(base) != gateway_request_fingerprint(sensitive)
