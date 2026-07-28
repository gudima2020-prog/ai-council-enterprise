from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.api.dependencies import (
    get_council_control_service,
    get_db_session,
)
from backend.core.config import AppSettings
from backend.core.events import EventBus
from backend.council.models import (
    CouncilCostLedgerModel,
    CouncilRunMemberModel,
    CouncilRunModel,
)
from backend.council.schemas import (
    CouncilMemberRequest,
    CouncilRole,
    CouncilRunRequest,
)
from backend.council.service import CouncilExecutionError, CouncilService
from backend.gateway.providers.base import ProviderAdapter
from backend.gateway.schemas import (
    GatewayError,
    GatewayRequest,
    GatewayResponse,
    GatewayUsage,
)
from backend.gateway.service import AIGateway
from backend.database.base import Base
from backend.database.models import SettingModel, WorkspaceModel
from backend.routers import council as council_router


class AllowAllCouncilControl:
    async def authorize(self, request, workspace_id):
        return (
            request.model_copy(
                update={
                    "workspace_id": workspace_id,
                    "estimated_cost_usd": 0.0,
                    "cost_estimate_status": "known",
                }
            ),
            None,
        )


class FakeCouncilProvider(ProviderAdapter):
    name = "fake"

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        if request.model.startswith("broken"):
            return GatewayResponse(
                request_id=request.request_id,
                provider=self.name,
                model=request.model,
                content="",
                status="error",
                error=GatewayError(
                    code="MODEL_UNAVAILABLE",
                    message="Model unavailable.",
                    provider=self.name,
                    recoverable=True,
                ),
            )

        if request.source == "council_synthesis":
            content = json.dumps(
                {
                    "final_answer": "Сводный итог Совета.",
                    "consensus": ["Участники согласны по главному выводу."],
                    "disagreements": ["Различается оценка риска."],
                    "recommendations": ["Проверить исходные данные."],
                    "confidence": 82,
                },
                ensure_ascii=False,
            )
        else:
            content = f"Независимый ответ модели {request.model}."

        return GatewayResponse(
            request_id=request.request_id,
            provider=self.name,
            model=request.model,
            content=content,
            status="success",
            usage=GatewayUsage(total_tokens=10),
            latency_ms=1.0,
        )


class FreeFallbackProvider(ProviderAdapter):
    name = "openrouter"

    def __init__(
        self,
        *,
        fallback_fails: bool = False,
        synthesis_rate_limited: bool = False,
    ) -> None:
        self.fallback_fails = fallback_fails
        self.synthesis_rate_limited = synthesis_rate_limited
        self.requests: list[tuple[str, str]] = []

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        self.requests.append((request.model, request.source))
        if request.model.startswith("rate-limited"):
            return self._error(request, "RATE_LIMIT")
        if (
            self.synthesis_rate_limited
            and request.source == "council_synthesis"
        ):
            return self._error(request, "RATE_LIMIT")
        if (
            self.fallback_fails
            and request.model == "openrouter/free"
            and request.source.endswith("_free_fallback")
        ):
            return self._error(request, "TIMEOUT")

        if request.source.startswith("council_synthesis"):
            content = json.dumps(
                {
                    "finalAnswer": "Сводный итог через резервный маршрут.",
                    "consensus": "Главный вывод подтверждён.",
                    "disagreements": [],
                    "recommendations": ["Проверить результат."],
                    "confidence": "79",
                },
                ensure_ascii=False,
            )
        else:
            content = f"Ответ через {request.model}."
        return GatewayResponse(
            request_id=request.request_id,
            provider=self.name,
            model=request.model,
            content=content,
            status="success",
            usage=GatewayUsage(total_tokens=7),
            latency_ms=1.25,
        )

    def _error(
        self,
        request: GatewayRequest,
        code: str,
    ) -> GatewayResponse:
        return GatewayResponse(
            request_id=request.request_id,
            provider=self.name,
            model=request.model,
            content="",
            status="error",
            latency_ms=1.25,
            error=GatewayError(
                code=code,
                message=f"Temporary provider error: {code}.",
                provider=self.name,
                recoverable=True,
            ),
        )


def make_settings() -> AppSettings:
    return AppSettings(
        app_name="Test",
        app_version="0",
        environment="test",
        debug=True,
        host="127.0.0.1",
        port=8000,
        openrouter_api_key="",
        default_provider="fake",
        default_model="model-a",
        max_tokens=1000,
        temperature=0.2,
        request_timeout_seconds=10,
    )


def make_gateway(event_bus: EventBus) -> AIGateway:
    return AIGateway(
        settings=make_settings(),
        event_bus=event_bus,
        providers={"fake": FakeCouncilProvider()},
    )


def make_openrouter_gateway(
    event_bus: EventBus,
    provider: FreeFallbackProvider,
) -> AIGateway:
    return AIGateway(
        settings=make_settings(),
        event_bus=event_bus,
        providers={"openrouter": provider},
    )


def make_request(*models: str) -> CouncilRunRequest:
    roles = [
        CouncilRole.ANALYST,
        CouncilRole.CRITIC,
        CouncilRole.STRATEGIST,
        CouncilRole.RISK,
    ]
    return CouncilRunRequest(
        question="Какой вариант выбрать?",
        members=[
            CouncilMemberRequest(
                provider="fake",
                model=model,
                role=roles[index],
            )
            for index, model in enumerate(models)
        ],
    )


@pytest.mark.asyncio
async def test_council_returns_members_and_structured_synthesis() -> None:
    event_bus = EventBus()
    service = CouncilService(
        gateway=make_gateway(event_bus),
        event_bus=event_bus,
    )

    result = await service.run(
        make_request("model-a", "model-b", "model-c")
    )

    assert result.status == "completed"
    assert [member.status for member in result.members] == [
        "success",
        "success",
        "success",
    ]
    assert result.synthesis.status == "structured"
    assert result.synthesis.final_answer == "Сводный итог Совета."
    assert result.synthesis.confidence == 82
    assert result.synthesis.consensus
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_council_preserves_partial_results_when_one_model_fails() -> None:
    event_bus = EventBus()
    service = CouncilService(
        gateway=make_gateway(event_bus),
        event_bus=event_bus,
    )

    result = await service.run(
        make_request("model-a", "broken-model", "model-c")
    )

    assert result.status == "partial"
    assert [member.status for member in result.members].count("success") == 2
    assert [member.status for member in result.members].count("error") == 1
    assert result.synthesis.status == "structured"


@pytest.mark.asyncio
async def test_council_uses_free_router_after_transient_free_model_error() -> None:
    event_bus = EventBus()
    provider = FreeFallbackProvider()
    service = CouncilService(
        gateway=make_openrouter_gateway(event_bus, provider),
        event_bus=event_bus,
    )
    request = CouncilRunRequest(
        question="Какой вариант выбрать?",
        members=[
            CouncilMemberRequest(
                provider="openrouter",
                model="rate-limited-model:free",
                role=CouncilRole.ANALYST,
            ),
            CouncilMemberRequest(
                provider="openrouter",
                model="stable-model:free",
                role=CouncilRole.CRITIC,
            ),
        ],
    )

    result = await service.run(request)

    recovered = result.members[0]
    assert result.status == "completed"
    assert recovered.status == "success"
    assert recovered.requested_model == "rate-limited-model:free"
    assert recovered.model == "openrouter/free"
    assert recovered.fallback_used is True
    assert recovered.fallback_model == "openrouter/free"
    assert recovered.latency_ms == 2.5
    assert (
        "openrouter/free",
        "council_member_free_fallback",
    ) in provider.requests


@pytest.mark.asyncio
async def test_council_does_not_retry_metered_model_and_keeps_partial_result() -> None:
    event_bus = EventBus()
    provider = FreeFallbackProvider()
    service = CouncilService(
        gateway=make_openrouter_gateway(event_bus, provider),
        event_bus=event_bus,
    )
    request = CouncilRunRequest(
        question="Какой вариант выбрать?",
        members=[
            CouncilMemberRequest(
                provider="openrouter",
                model="rate-limited-paid-model",
                role=CouncilRole.ANALYST,
            ),
            CouncilMemberRequest(
                provider="openrouter",
                model="stable-model:free",
                role=CouncilRole.CRITIC,
            ),
        ],
    )

    result = await service.run(request)

    failed = result.members[0]
    assert result.status == "partial"
    assert failed.status == "error"
    assert failed.fallback_used is False
    assert provider.requests.count(
        ("rate-limited-paid-model", "council_member")
    ) == 1


@pytest.mark.asyncio
async def test_council_keeps_partial_result_when_free_fallback_also_fails() -> None:
    event_bus = EventBus()
    provider = FreeFallbackProvider(fallback_fails=True)
    service = CouncilService(
        gateway=make_openrouter_gateway(event_bus, provider),
        event_bus=event_bus,
    )
    request = CouncilRunRequest(
        question="Какой вариант выбрать?",
        members=[
            CouncilMemberRequest(
                provider="openrouter",
                model="rate-limited-model:free",
                role=CouncilRole.ANALYST,
            ),
            CouncilMemberRequest(
                provider="openrouter",
                model="stable-model:free",
                role=CouncilRole.CRITIC,
            ),
        ],
    )

    result = await service.run(request)

    failed = result.members[0]
    assert result.status == "partial"
    assert failed.status == "error"
    assert failed.fallback_used is True
    assert failed.error_code == "TIMEOUT"
    assert "RATE_LIMIT" in (failed.error_message or "")
    assert "openrouter/free" in (failed.error_message or "")


@pytest.mark.asyncio
async def test_council_recovers_synthesis_through_free_router() -> None:
    event_bus = EventBus()
    provider = FreeFallbackProvider(synthesis_rate_limited=True)
    service = CouncilService(
        gateway=make_openrouter_gateway(event_bus, provider),
        event_bus=event_bus,
    )
    request = CouncilRunRequest(
        question="Какой вариант выбрать?",
        members=[
            CouncilMemberRequest(
                provider="openrouter",
                model="stable-model-a:free",
                role=CouncilRole.ANALYST,
            ),
            CouncilMemberRequest(
                provider="openrouter",
                model="stable-model-b:free",
                role=CouncilRole.CRITIC,
            ),
        ],
    )

    result = await service.run(request)

    assert result.status == "completed"
    assert result.synthesis.status == "structured"
    assert result.synthesis.fallback_used is True
    assert result.synthesis.requested_model == "stable-model-a:free"
    assert result.synthesis.model == "openrouter/free"
    assert (
        "openrouter/free",
        "council_synthesis_free_fallback",
    ) in provider.requests


@pytest.mark.asyncio
async def test_council_fails_only_after_all_members_fail() -> None:
    event_bus = EventBus()
    service = CouncilService(
        gateway=make_gateway(event_bus),
        event_bus=event_bus,
    )

    with pytest.raises(CouncilExecutionError) as error:
        await service.run(make_request("broken-a", "broken-b"))

    assert len(error.value.member_results) == 2
    assert all(
        member.status == "error"
        for member in error.value.member_results
    )


def test_council_rejects_duplicate_provider_model_pairs() -> None:
    with pytest.raises(ValidationError, match="distinct provider/model"):
        make_request("model-a", "model-a")


def test_synthesis_parser_accepts_double_encoded_json() -> None:
    payload = {
        "final_answer": "Нормализованный итог.",
        "consensus": ["Общий вывод."],
        "disagreements": [],
        "recommendations": ["Следующий шаг."],
        "confidence": 84,
    }
    content = json.dumps(
        json.dumps(payload, ensure_ascii=False),
        ensure_ascii=False,
    )

    parsed = CouncilService._parse_synthesis(content)

    assert parsed is not None
    assert parsed["final_answer"] == "Нормализованный итог."
    assert parsed["confidence"] == 84


def test_synthesis_parser_accepts_common_schema_variants() -> None:
    content = """```json
    {'finalAnswer': 'Итог без сырого JSON',
     'consensus': 'Главное подтверждено',
     'recommendations': ['Проверить данные'],
     'confidence': '105'}
    ```"""

    parsed = CouncilService._parse_synthesis(content)

    assert parsed is not None
    assert parsed["final_answer"] == "Итог без сырого JSON"
    assert parsed["consensus"] == ["Главное подтверждено"]
    assert parsed["confidence"] == 100


def test_synthesis_parser_extracts_answer_from_malformed_payload() -> None:
    content = (
        '{"final_answer": "Читаемый итог", '
        '"consensus": [broken response]}'
    )

    assert CouncilService._parse_synthesis(content) is None
    assert CouncilService._extract_final_answer(content) == "Читаемый итог"


def test_council_api_exposes_status_and_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_bus = EventBus()
    gateway = make_gateway(event_bus)
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            WorkspaceModel.__table__,
            SettingModel.__table__,
            CouncilRunModel.__table__,
            CouncilRunMemberModel.__table__,
            CouncilCostLedgerModel.__table__,
        ],
    )

    def override_db_session():
        with Session(engine) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    api = FastAPI()
    api.state.container = SimpleNamespace(
        settings=make_settings(),
        event_bus=event_bus,
        secret_manager_service=None,
    )
    api.include_router(council_router.router, prefix="/api")
    monkeypatch.setattr(
        council_router,
        "build_ai_gateway",
        lambda **_: gateway,
    )
    api.dependency_overrides[get_db_session] = override_db_session
    api.dependency_overrides[get_council_control_service] = (
        lambda: AllowAllCouncilControl()
    )

    with TestClient(api) as client:
        status_response = client.get("/api/council/status")
        retention_response = client.get("/api/council/retention")
        retention_update_response = client.put(
            "/api/council/retention",
            json={
                "retention_days": 30,
                "auto_delete_enabled": False,
                "store_member_answers": True,
                "store_token_usage": True,
            },
        )
        retention_purge_response = client.post(
            "/api/council/retention/purge"
        )
        run_response = client.post(
            "/api/council/run",
            json=make_request(
                "model-a",
                "model-b",
                "model-c",
            ).model_dump(mode="json"),
        )
        run_id = run_response.json()["run_id"]
        history_response = client.get("/api/council/runs")
        detail_response = client.get(f"/api/council/runs/{run_id}")
        replay_response = client.post(
            f"/api/council/runs/{run_id}/replay"
        )
        replay_run_id = replay_response.json()["run_id"]
        delete_response = client.delete(f"/api/council/runs/{run_id}")
        deleted_detail_response = client.get(
            f"/api/council/runs/{run_id}"
        )
        replay_detail_response = client.get(
            f"/api/council/runs/{replay_run_id}"
        )
        failed_response = client.post(
            "/api/council/run",
            json=make_request(
                "broken-a",
                "broken-b",
            ).model_dump(mode="json"),
        )
        failed_history_response = client.get(
            "/api/council/runs?status=failed"
        )
        failed_run_id = failed_response.json()["detail"]["run_id"]
        failed_detail_response = client.get(
            f"/api/council/runs/{failed_run_id}"
        )

    assert status_response.status_code == 200
    assert status_response.json()["min_members"] == 1
    assert status_response.json()["supports_history"] is True
    assert status_response.json()["supports_retention_policy"] is True
    assert set(status_response.json()["execution_modes"]) == {
        "solo", "council", "best_of_n", "review", "arbitration", "delegate"
    }
    assert retention_response.status_code == 200
    assert retention_response.json()["retention_days"] == 365
    assert retention_update_response.status_code == 200
    assert retention_update_response.json()["retention_days"] == 30
    assert retention_purge_response.status_code == 200
    assert retention_purge_response.json()["deleted_count"] == 0
    assert run_response.status_code == 200
    assert run_response.json()["status"] == "completed"
    assert run_response.json()["synthesis"]["confidence"] == 82
    assert run_response.json()["history_saved"] is True
    assert history_response.status_code == 200
    assert history_response.json()["total"] == 1
    assert detail_response.status_code == 200
    assert detail_response.json()["run_id"] == run_id
    assert replay_response.status_code == 200
    assert replay_response.json()["replay_of_run_id"] == run_id
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True
    assert deleted_detail_response.status_code == 404
    assert replay_detail_response.status_code == 200
    assert replay_detail_response.json()["replay_of_run_id"] is None
    assert failed_response.status_code == 502
    assert failed_response.json()["detail"]["history_saved"] is True
    assert failed_history_response.status_code == 200
    assert failed_history_response.json()["total"] == 1
    assert failed_detail_response.status_code == 200
    assert failed_detail_response.json()["status"] == "failed"
    assert failed_detail_response.json()["synthesis"] is None
