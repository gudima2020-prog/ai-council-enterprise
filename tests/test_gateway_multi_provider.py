from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.config import AppSettings
from backend.core.events import EventBus
from backend.database.base import Base
from backend.gateway.health import ProviderHealthService, TransientProviderHealthService
from backend.gateway.providers.base import ProviderAdapter
from backend.gateway.routing import GatewayRoutingService, RoutingRequirements
from backend.gateway.schemas import GatewayError, GatewayRequest, GatewayResponse, GatewayUsage
from backend.gateway.service import AIGateway
from backend.repositories.models import ModelRepository


class SequenceProvider(ProviderAdapter):
    def __init__(self, name: str, responses: list[GatewayResponse | str]) -> None:
        self.name = name
        self.responses = list(responses)
        self.requests: list[GatewayRequest] = []

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, str):
            return GatewayResponse(
                request_id=request.request_id,
                provider=self.name,
                model=request.model,
                content=item,
                status="success",
                usage=GatewayUsage(total_tokens=5),
                latency_ms=12.0,
            )
        return GatewayResponse(
            request_id=request.request_id,
            provider=self.name,
            model=request.model,
            content=item.content,
            status=item.status,
            error=item.error,
            latency_ms=item.latency_ms,
        )


def settings() -> AppSettings:
    return AppSettings(
        app_name="Test",
        app_version="0",
        environment="test",
        debug=True,
        host="127.0.0.1",
        port=8000,
        openrouter_api_key="",
        default_provider="primary",
        default_model="primary-model",
        max_tokens=100,
        temperature=0.1,
        request_timeout_seconds=10,
    )


def server_error(provider: str = "primary") -> GatewayResponse:
    return GatewayResponse(
        request_id="ignored",
        provider=provider,
        model="model",
        content="",
        status="error",
        latency_ms=20.0,
        error=GatewayError(
            code="SERVER_ERROR",
            message="temporary",
            provider=provider,
            recoverable=True,
        ),
    )


@pytest.mark.asyncio
async def test_gateway_fails_over_only_on_configured_route() -> None:
    primary = SequenceProvider("primary", [server_error()])
    backup = SequenceProvider("backup", ["backup answer"])
    health = TransientProviderHealthService()
    gateway = AIGateway(
        settings=settings(),
        event_bus=EventBus(),
        providers={"primary": primary, "backup": backup},
        fallback_routes={"primary": [("backup", "backup-model")]},
        health_service=health,
    )

    response = await gateway.ask(
        user_prompt="hello",
        system_prompt="system",
        source="test",
    )

    assert response.status == "success"
    assert response.provider == "backup"
    assert response.model == "backup-model"
    assert response.metadata["failover_used"] is True
    assert len(primary.requests) == 1
    assert len(backup.requests) == 1
    assert health.snapshot("primary")["failed_requests"] == 1
    assert health.snapshot("backup")["successful_requests"] == 1


@pytest.mark.asyncio
async def test_nonrecoverable_error_does_not_fail_over() -> None:
    primary = SequenceProvider(
        "primary",
        [
            GatewayResponse(
                request_id="ignored",
                provider="primary",
                model="primary-model",
                content="",
                status="error",
                error=GatewayError(
                    code="BAD_REQUEST",
                    message="bad request",
                    provider="primary",
                    recoverable=False,
                ),
            )
        ],
    )
    backup = SequenceProvider("backup", ["should not run"])
    gateway = AIGateway(
        settings=settings(),
        event_bus=EventBus(),
        providers={"primary": primary, "backup": backup},
        fallback_routes={"primary": [("backup", "backup-model")]},
    )

    response = await gateway.ask(
        user_prompt="hello",
        system_prompt="system",
        source="test",
    )

    assert response.status == "error"
    assert response.error and response.error.code == "BAD_REQUEST"
    assert backup.requests == []


@pytest.mark.asyncio
async def test_circuit_breaker_skips_failed_provider_after_threshold() -> None:
    primary = SequenceProvider(
        "primary",
        [server_error(), server_error(), server_error()],
    )
    backup = SequenceProvider("backup", ["one", "two", "three", "four"])
    health = TransientProviderHealthService(failure_threshold=3)
    gateway = AIGateway(
        settings=settings(),
        event_bus=EventBus(),
        providers={"primary": primary, "backup": backup},
        fallback_routes={"primary": [("backup", "backup-model")]},
        health_service=health,
    )
    for _ in range(3):
        response = await gateway.ask(
            user_prompt="hello",
            system_prompt="system",
            source="test",
        )
        assert response.status == "success"
    assert health.snapshot("primary")["circuit_open"] is True

    fourth = await gateway.ask(
        user_prompt="hello",
        system_prompt="system",
        source="test",
    )
    assert fourth.status == "success"
    assert len(primary.requests) == 3
    assert len(backup.requests) == 4


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

    return engine, scope


def test_provider_health_is_persisted() -> None:
    _engine, scope = make_scope()
    service = ProviderHealthService(
        session_factory=scope,
        failure_threshold=2,
        cooldown_seconds=60,
    )
    service.record_failure(
        "svrtr", 100.0, "SERVER_ERROR", circuit_eligible=True
    )
    service.record_failure(
        "svrtr", 200.0, "SERVER_ERROR", circuit_eligible=True
    )
    snapshot = service.snapshot("svrtr")
    assert snapshot["circuit_open"] is True
    assert snapshot["failed_requests"] == 2
    assert snapshot["ema_latency_ms"] == pytest.approx(120.0)


def test_router_combines_price_latency_reliability_and_quality() -> None:
    engine, scope = make_scope()
    health = ProviderHealthService(session_factory=scope)
    health.record_success("cheap", 300.0)
    health.record_success("strong", 1500.0)
    with Session(engine) as session:
        repo = ModelRepository(session)
        repo.upsert(
            provider="cheap",
            slug="cheap-model",
            display_name="Cheap",
            enabled=True,
            priority=10,
            context_window=100000,
            max_output_tokens=1000,
            supports_tools=True,
            supports_vision=False,
            supports_json=True,
            metadata_json={
                "billing": "metered",
                "quality_score": 70,
                "pricing": {
                    "input_per_million_usd": 1.0,
                    "output_per_million_usd": 2.0,
                },
            },
        )
        repo.upsert(
            provider="strong",
            slug="strong-model",
            display_name="Strong",
            enabled=True,
            priority=20,
            context_window=100000,
            max_output_tokens=1000,
            supports_tools=True,
            supports_vision=True,
            supports_json=True,
            metadata_json={
                "billing": "metered",
                "quality_score": 98,
                "pricing": {
                    "input_per_million_usd": 10.0,
                    "output_per_million_usd": 30.0,
                },
            },
        )
        session.commit()
        router = GatewayRoutingService(session=session, health_service=health)
        cheapest = router.recommend(
            strategy="cost",
            requirements=RoutingRequirements(require_tools=True),
            limit=2,
        )
        quality = router.recommend(
            strategy="quality",
            requirements=RoutingRequirements(require_tools=True),
            limit=2,
        )
        assert cheapest[0]["model"] == "cheap-model"
        assert quality[0]["model"] == "strong-model"
        restricted = router.recommend(
            strategy="balanced",
            requirements=RoutingRequirements(),
            limit=5,
            allowed_providers={"strong"},
        )
        assert [item["provider"] for item in restricted] == ["strong"]


@pytest.mark.asyncio
async def test_health_storage_failure_never_breaks_successful_ai_response() -> None:
    class BrokenHealth:
        def circuit_available(self, provider: str) -> bool:
            raise RuntimeError("db locked")

        def record_success(self, provider: str, latency_ms: float | None) -> None:
            raise RuntimeError("db locked")

        def record_failure(self, *args, **kwargs) -> None:
            raise RuntimeError("db locked")

    provider = SequenceProvider("primary", ["answer"])
    gateway = AIGateway(
        settings=settings(),
        event_bus=EventBus(),
        providers={"primary": provider},
        health_service=BrokenHealth(),  # type: ignore[arg-type]
    )
    response = await gateway.ask(
        user_prompt="hello",
        system_prompt="system",
        source="test",
    )
    assert response.status == "success"
    assert response.content == "answer"
