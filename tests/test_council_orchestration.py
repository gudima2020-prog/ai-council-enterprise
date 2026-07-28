from __future__ import annotations

import asyncio
import json

import pytest

from backend.core.config import AppSettings
from backend.core.events import EventBus
from backend.council.schemas import (
    CouncilExecutionMode,
    CouncilMemberRequest,
    CouncilRole,
    CouncilRunRequest,
)
from backend.council.service import CouncilCancelledError, CouncilService
from backend.gateway.providers.base import ProviderAdapter
from backend.gateway.schemas import GatewayRequest, GatewayResponse, GatewayUsage
from backend.gateway.service import AIGateway


class OrchestrationProvider(ProviderAdapter):
    name = "fake"

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        self.requests.append((request.model, request.source))
        if request.source == "council_delegation_planner":
            content = json.dumps(
                {
                    "tasks": [
                        {"title": "Проверка A", "prompt": "Проверь допущение A."},
                        {"title": "Проверка B", "prompt": "Проверь риск B."},
                        {"title": "Лишняя", "prompt": "Эта задача должна быть обрезана лимитом."},
                    ]
                },
                ensure_ascii=False,
            )
        elif request.source == "council_reviewer":
            content = json.dumps(
                {
                    "verdict": "revise",
                    "strengths": ["структура"],
                    "weaknesses": ["не хватает проверки"],
                    "required_changes": ["добавить проверку"],
                    "confidence": 88,
                },
                ensure_ascii=False,
            )
        elif request.source in {
            "council_synthesis",
            "council_best_of_n",
            "council_review_draft",
            "council_review_revision",
            "council_arbiter",
            "council_delegate_synthesis",
        }:
            content = json.dumps(
                {
                    "final_answer": f"Итог через {request.source}",
                    "consensus": ["общий вывод"],
                    "disagreements": [],
                    "recommendations": ["действовать"],
                    "confidence": 84,
                },
                ensure_ascii=False,
            )
        else:
            content = f"Ответ {request.model} для {request.source}"
        return GatewayResponse(
            request_id=request.request_id,
            provider=self.name,
            model=request.model,
            content=content,
            status="success",
            usage=GatewayUsage(input_tokens=10, output_tokens=20, total_tokens=30),
            cost=0.001,
            latency_ms=1.0,
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
        default_provider="fake",
        default_model="model-a",
        max_tokens=1000,
        temperature=0.2,
        request_timeout_seconds=10,
    )


def service(provider: OrchestrationProvider) -> CouncilService:
    bus = EventBus()
    gateway = AIGateway(settings=settings(), event_bus=bus, providers={"fake": provider})
    return CouncilService(gateway=gateway, event_bus=bus)


def members(count: int = 2) -> list[CouncilMemberRequest]:
    values = []
    roles = [CouncilRole.ANALYST, CouncilRole.CRITIC, CouncilRole.STRATEGIST]
    for index in range(count):
        values.append(
            CouncilMemberRequest(
                provider="fake",
                model=f"member-{index + 1}",
                role=roles[index % len(roles)],
            )
        )
    return values


@pytest.mark.asyncio
async def test_solo_uses_exactly_one_model_without_synthesis_call() -> None:
    provider = OrchestrationProvider()
    result = await service(provider).run(
        CouncilRunRequest(
            question="Ответь сам.",
            execution_mode=CouncilExecutionMode.SOLO,
            members=members(1),
        )
    )

    assert result.execution_mode == CouncilExecutionMode.SOLO
    assert result.orchestration.finalizer_stage == "solo"
    assert result.synthesis.final_answer.startswith("Ответ member-1")
    assert provider.requests == [("member-1", "council_member")]


@pytest.mark.asyncio
async def test_best_of_n_uses_dedicated_selector() -> None:
    provider = OrchestrationProvider()
    result = await service(provider).run(
        CouncilRunRequest(
            question="Выбери лучший вариант.",
            execution_mode=CouncilExecutionMode.BEST_OF_N,
            members=members(),
            synthesizer_provider="fake",
            synthesizer_model="chair",
        )
    )

    assert result.orchestration.finalizer_stage == "selection"
    assert ("chair", "council_best_of_n") in provider.requests


@pytest.mark.asyncio
async def test_review_uses_independent_reviewer_then_revision() -> None:
    provider = OrchestrationProvider()
    result = await service(provider).run(
        CouncilRunRequest(
            question="Проверь решение.",
            execution_mode=CouncilExecutionMode.REVIEW,
            members=members(),
            synthesizer_provider="fake",
            synthesizer_model="chair",
            reviewer_provider="fake",
            reviewer_model="reviewer",
        )
    )

    assert result.orchestration.finalizer_stage == "revision"
    assert [item.stage for item in result.orchestration.auxiliary_calls] == [
        "draft",
        "reviewer",
    ]
    assert ("chair", "council_review_draft") in provider.requests
    assert ("reviewer", "council_reviewer") in provider.requests
    assert ("chair", "council_review_revision") in provider.requests


@pytest.mark.asyncio
async def test_arbitration_keeps_reviewer_and_arbiter_independent() -> None:
    provider = OrchestrationProvider()
    result = await service(provider).run(
        CouncilRunRequest(
            question="Разреши спор.",
            execution_mode=CouncilExecutionMode.ARBITRATION,
            members=members(),
            synthesizer_provider="fake",
            synthesizer_model="chair",
            reviewer_provider="fake",
            reviewer_model="reviewer",
            arbiter_provider="fake",
            arbiter_model="arbiter",
        )
    )

    assert result.orchestration.finalizer_stage == "arbiter"
    assert [item.stage for item in result.orchestration.auxiliary_calls] == ["reviewer"]
    assert ("reviewer", "council_reviewer") in provider.requests
    assert ("arbiter", "council_arbiter") in provider.requests


@pytest.mark.asyncio
async def test_delegate_is_depth_one_and_hard_caps_subagent_calls() -> None:
    provider = OrchestrationProvider()
    result = await service(provider).run(
        CouncilRunRequest(
            question="Разложи сложную задачу.",
            execution_mode=CouncilExecutionMode.DELEGATE,
            members=members(),
            synthesizer_provider="fake",
            synthesizer_model="chair",
            delegation_max_calls=2,
        )
    )

    delegates = [item for item in result.orchestration.auxiliary_calls if item.stage == "delegate"]
    assert result.orchestration.delegation_depth == 1
    assert result.orchestration.delegation_calls == 2
    assert len(delegates) == 2
    assert all(item.metadata.get("depth") == 1 for item in delegates)
    assert provider.requests.count(("member-1", "council_delegate")) == 1
    assert provider.requests.count(("member-2", "council_delegate")) == 1
    assert ("chair", "council_delegate_synthesis") in provider.requests



@pytest.mark.asyncio
async def test_review_cancellation_preserves_spent_auxiliary_calls() -> None:
    provider = OrchestrationProvider()
    cancel_event = asyncio.Event()

    async def progress(event_type: str, data: dict[str, object]) -> None:
        del data
        if event_type == "reviewer.completed":
            cancel_event.set()

    request = CouncilRunRequest(
        question="Отмени после оплаченного review.",
        execution_mode=CouncilExecutionMode.REVIEW,
        members=members(),
        synthesizer_provider="fake",
        synthesizer_model="chair",
        reviewer_provider="fake",
        reviewer_model="reviewer",
    )

    with pytest.raises(CouncilCancelledError) as exc_info:
        await service(provider).run(
            request,
            progress=progress,
            cancel_event=cancel_event,
        )

    assert [item.stage for item in exc_info.value.auxiliary_results] == [
        "draft",
        "reviewer",
    ]
    assert ("chair", "council_review_revision") not in provider.requests

@pytest.mark.asyncio
async def test_review_cancellation_at_revision_started_blocks_provider_call() -> None:
    provider = OrchestrationProvider()
    cancel_event = asyncio.Event()

    async def progress(event_type: str, data: dict[str, object]) -> None:
        del data
        if event_type == "revision.started":
            cancel_event.set()

    request = CouncilRunRequest(
        question="Отмени на границе revision до запроса провайдеру.",
        execution_mode=CouncilExecutionMode.REVIEW,
        members=members(),
        synthesizer_provider="fake",
        synthesizer_model="chair",
        reviewer_provider="fake",
        reviewer_model="reviewer",
    )

    with pytest.raises(CouncilCancelledError) as exc_info:
        await service(provider).run(
            request,
            progress=progress,
            cancel_event=cancel_event,
        )

    assert [item.stage for item in exc_info.value.auxiliary_results] == [
        "draft",
        "reviewer",
    ]
    assert ("chair", "council_review_revision") not in provider.requests

def test_reviewer_and_arbiter_cannot_be_writers() -> None:
    with pytest.raises(ValueError, match="Reviewer must be independent"):
        CouncilRunRequest(
            question="Нельзя совмещать роли.",
            execution_mode=CouncilExecutionMode.REVIEW,
            members=members(),
            synthesizer_provider="fake",
            synthesizer_model="chair",
            reviewer_provider="fake",
            reviewer_model="member-1",
        )

@pytest.mark.asyncio
async def test_review_cost_ledger_includes_draft_reviewer_and_finalizer() -> None:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from backend.council.history import CouncilHistoryService
    from backend.council.models import CouncilCostLedgerModel
    from backend.council.repository import CouncilRunRepository
    from backend.database.base import Base

    provider = OrchestrationProvider()
    request = CouncilRunRequest(
        question="Проверь стоимость полного review pipeline.",
        execution_mode=CouncilExecutionMode.REVIEW,
        members=members(),
        synthesizer_provider="fake",
        synthesizer_model="chair",
        reviewer_provider="fake",
        reviewer_model="reviewer",
    )
    result = await service(provider).run(request)

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        history = CouncilHistoryService(
            repository=CouncilRunRepository(session),
            event_bus=EventBus(),
        )
        detail = await history.record_success(request=request, result=result)
        session.commit()
        rows = list(
            session.scalars(
                select(CouncilCostLedgerModel)
                .where(CouncilCostLedgerModel.run_id == result.run_id)
                .order_by(CouncilCostLedgerModel.line_key)
            ).all()
        )

        assert detail.execution_mode == CouncilExecutionMode.REVIEW
        assert detail.orchestration.finalizer_stage == "revision"
        assert {row.kind for row in rows} == {"member", "draft", "reviewer", "synthesis"}
        assert len(rows) == 5
        assert detail.actual_cost_usd == pytest.approx(0.005)


def test_advanced_cost_preflight_reserves_delegate_worst_case() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from backend.council.control import CouncilControlService
    from backend.database.base import Base
    from backend.database.models import ModelConfigModel

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                ModelConfigModel(
                    provider="fake",
                    slug=slug,
                    display_name=slug,
                    metadata_json={
                        "billing": "metered",
                        "pricing": {
                            "input_per_million_usd": 1.0,
                            "output_per_million_usd": 2.0,
                        },
                    },
                )
                for slug in ("member-1", "member-2", "chair")
            ]
        )
        session.commit()
        control = CouncilControlService(session=session, event_bus=EventBus())
        estimate = control.estimate(
            CouncilRunRequest(
                question="Сложная задача",
                execution_mode=CouncilExecutionMode.DELEGATE,
                members=members(),
                synthesizer_provider="fake",
                synthesizer_model="chair",
                delegation_max_calls=3,
            ),
            None,
        )

        assert [line.kind for line in estimate.lines].count("delegate") == 3
        assert [line.kind for line in estimate.lines].count("planner") == 1
        assert estimate.lines[-1].kind == "synthesis"
        assert estimate.estimate_status == "known"
        assert estimate.estimated_cost_usd is not None
