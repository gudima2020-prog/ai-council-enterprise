import pytest

from backend.core.config import AppSettings
from backend.core.events import EventBus
from backend.gateway.providers.base import ProviderAdapter
from backend.gateway.schemas import (
    GatewayRequest,
    GatewayResponse,
    GatewayUsage,
)
from backend.gateway.service import AIGateway


class FakeProvider(ProviderAdapter):
    name = "fake"

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        return GatewayResponse(
            request_id=request.request_id,
            provider="fake",
            model=request.model,
            content="ok",
            status="success",
            usage=GatewayUsage(total_tokens=10),
            latency_ms=1.0,
        )


@pytest.mark.asyncio
async def test_gateway_returns_normalized_response() -> None:
    settings = AppSettings(
        app_name="Test",
        app_version="0",
        environment="test",
        debug=True,
        host="127.0.0.1",
        port=8000,
        openrouter_api_key="",
        default_provider="fake",
        default_model="fake-model",
        max_tokens=100,
        temperature=0.1,
        request_timeout_seconds=10,
    )

    gateway = AIGateway(
        settings=settings,
        event_bus=EventBus(),
        providers={"fake": FakeProvider()},
    )

    response = await gateway.ask(
        user_prompt="hello",
        system_prompt="system",
        source="test",
    )

    assert response.status == "success"
    assert response.content == "ok"
    assert response.provider == "fake"
