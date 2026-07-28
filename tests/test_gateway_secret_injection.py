from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.config import AppSettings
from backend.database.base import Base
from backend.database import models as database_models  # noqa: F401
from backend.secrets import models as secret_models  # noqa: F401
from backend.gateway.providers.base import ProviderAdapter
from backend.gateway.schemas import GatewayRequest, GatewayResponse, GatewayUsage
from backend.gateway.service import AIGateway
from backend.secrets.schemas import SecretCreate
from backend.secrets.service import SecretManagerService


class FakeEventBus:
    async def publish(self, *args, **kwargs) -> None:
        return None


class FakeProvider(ProviderAdapter):
    name = "fake"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        return GatewayResponse(
            request_id=request.request_id,
            provider="fake",
            model=request.model,
            content=f"provider debug accidentally contains {self.api_key}",
            status="success",
            usage=GatewayUsage(total_tokens=1),
            latency_ms=1.0,
        )


def make_scope():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

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

    return scope


@pytest.mark.asyncio
async def test_gateway_resolves_api_key_through_ephemeral_lease(monkeypatch) -> None:
    scope = make_scope()
    event_bus = FakeEventBus()
    manager = SecretManagerService(event_bus=event_bus, session_factory=scope)
    manager.seed_builtin_providers()
    manager.seed_access_defaults()
    monkeypatch.setenv("FAKE_GATEWAY_KEY", "gateway-secret-value")
    secret = await manager.create_secret(
        SecretCreate(
            provider_key="env",
            secret_key="FAKE_GATEWAY_KEY",
            provider_ref="FAKE_GATEWAY_KEY",
            display_name="Gateway fake key",
            created_by="owner",
        )
    )

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
        event_bus=event_bus,
        providers={},
        secret_manager=manager,
        provider_secret_refs={"fake": secret["reference"]},
        provider_factories={"fake": lambda key: FakeProvider(key)},
    )

    response = await gateway.ask(
        user_prompt="hello",
        system_prompt="system",
        source="test",
        actor_id="gateway-test",
    )
    assert response.status == "success"
    assert "gateway-secret-value" not in response.content
    assert "[REDACTED_SECRET]" in response.content
