from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.core.events import EventBus
from backend.council.history import CouncilHistoryService
from backend.council.models import (
    CouncilCostLedgerModel,
    CouncilRunMemberModel,
    CouncilRunModel,
)
from backend.council.repository import CouncilRunRepository
from backend.council.schemas import (
    CouncilAuxiliaryResult,
    CouncilMemberRequest,
    CouncilMemberResult,
    CouncilOrchestrationTrace,
    CouncilRetentionPolicyUpdate,
    CouncilRole,
    CouncilRunRequest,
    CouncilRunResponse,
    CouncilSynthesis,
    CouncilUsage,
)
from backend.council.service import (
    CouncilCancelledError,
    CouncilExecutionError,
)
from backend.database.base import Base
from backend.database.models import SettingModel, WorkspaceModel
from backend.repositories.settings import SettingsRepository


def _create_schema(engine) -> None:
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


def _request(workspace_id: str | None = "workspace_one") -> CouncilRunRequest:
    return CouncilRunRequest(
        question="Какой вариант архитектуры выбрать?",
        members=[
            CouncilMemberRequest(
                provider="fake",
                model="model-a",
                role=CouncilRole.ANALYST,
                label="Аналитик",
            ),
            CouncilMemberRequest(
                provider="fake",
                model="model-b",
                role=CouncilRole.CRITIC,
                label="Критик",
            ),
        ],
        mode="code",
        synthesizer_provider="fake",
        synthesizer_model="model-a",
        workspace_id=workspace_id,
        actor_id="tester",
    )


def _success_result(
    run_id: str = "council_history_success",
) -> CouncilRunResponse:
    started_at = datetime.now(timezone.utc)
    members = [
        CouncilMemberResult(
            provider="fake",
            model="model-a",
            requested_model="model-a",
            role=CouncilRole.ANALYST,
            label="Аналитик",
            status="success",
            answer="Ответ аналитика.",
            latency_ms=12.5,
            usage=CouncilUsage(total_tokens=10),
        ),
        CouncilMemberResult(
            provider="fake",
            model="model-b",
            requested_model="model-b",
            role=CouncilRole.CRITIC,
            label="Критик",
            status="success",
            answer="Ответ критика.",
            latency_ms=14.0,
            usage=CouncilUsage(total_tokens=11),
        ),
    ]
    return CouncilRunResponse(
        run_id=run_id,
        status="completed",
        question="Какой вариант архитектуры выбрать?",
        mode="code",
        members=members,
        synthesis=CouncilSynthesis(
            provider="fake",
            model="model-a",
            requested_model="model-a",
            status="structured",
            final_answer="Выбрать первый вариант.",
            consensus=["Главный вывод совпадает."],
            disagreements=["Разная оценка срока."],
            recommendations=["Провести прототипирование."],
            confidence=87,
        ),
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=1),
        duration_ms=1000.0,
    )


@pytest.mark.asyncio
async def test_history_persists_lists_replays_and_deletes() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    _create_schema(engine)

    with Session(engine) as session:
        session.add_all(
            [
                WorkspaceModel(id="workspace_one", name="One"),
                WorkspaceModel(id="workspace_two", name="Two"),
            ]
        )
        history = CouncilHistoryService(
            repository=CouncilRunRepository(session),
            event_bus=EventBus(),
        )
        await history.record_success(
            request=_request(),
            result=_success_result(),
        )
        await history.record_success(
            request=_request("workspace_two"),
            result=_success_result("council_other_workspace"),
        )
        session.commit()

        page = history.list_runs(workspace_id="workspace_one")
        detail = history.get_run(
            run_id="council_history_success",
            workspace_id="workspace_one",
        )
        hidden = history.get_run(
            run_id="council_other_workspace",
            workspace_id="workspace_one",
        )
        replay = history.build_replay_request(
            run_id="council_history_success",
            workspace_id="workspace_one",
        )

        assert page.total == 1
        assert page.items[0].confidence == 87
        assert detail is not None
        assert detail.synthesis is not None
        assert detail.synthesis.final_answer == "Выбрать первый вариант."
        assert detail.members[0].usage.total_tokens == 10
        assert hidden is None
        assert replay is not None
        assert replay.question == detail.question
        assert [member.model for member in replay.members] == [
            "model-a",
            "model-b",
        ]

        deleted = await history.delete_run(
            run_id="council_history_success",
            workspace_id="workspace_one",
        )
        session.commit()

        assert deleted is not None
        assert deleted.deleted is True
        assert history.get_run(
            run_id="council_history_success",
            workspace_id="workspace_one",
        ) is None




@pytest.mark.asyncio
async def test_history_preserves_advanced_orchestration_for_replay() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    _create_schema(engine)
    request = CouncilRunRequest(
        question="Проверь решение независимым ревью.",
        execution_mode="review",
        members=[
            CouncilMemberRequest(provider="fake", model="model-a", role=CouncilRole.ANALYST),
            CouncilMemberRequest(provider="fake", model="model-b", role=CouncilRole.CRITIC),
        ],
        synthesizer_provider="fake",
        synthesizer_model="model-a",
        reviewer_provider="fake",
        reviewer_model="model-c",
        workspace_id="workspace_one",
    )
    result = _success_result("council_history_review").model_copy(
        update={
            "execution_mode": "review",
            "orchestration": CouncilOrchestrationTrace(
                execution_mode="review",
                finalizer_stage="revision",
                auxiliary_calls=[
                    CouncilAuxiliaryResult(
                        stage="reviewer",
                        provider="fake",
                        model="model-c",
                        requested_model="model-c",
                        label="Independent Reviewer",
                        status="success",
                        content="Найдены два замечания.",
                        usage=CouncilUsage(total_tokens=7),
                    )
                ],
            ),
        }
    )

    with Session(engine) as session:
        session.add(WorkspaceModel(id="workspace_one", name="One"))
        history = CouncilHistoryService(
            repository=CouncilRunRepository(session),
            event_bus=EventBus(),
        )
        await history.record_success(request=request, result=result)
        session.commit()

        detail = history.get_run(
            run_id="council_history_review",
            workspace_id="workspace_one",
        )
        replay = history.build_replay_request(
            run_id="council_history_review",
            workspace_id="workspace_one",
        )

        assert detail is not None
        assert detail.execution_mode.value == "review"
        assert detail.orchestration.finalizer_stage == "revision"
        assert detail.orchestration.auxiliary_calls[0].stage == "reviewer"
        assert replay is not None
        assert replay.execution_mode.value == "review"
        assert replay.reviewer_model == "model-c"
        assert replay.synthesizer_model == "model-a"

@pytest.mark.asyncio
async def test_history_records_failed_runs_and_filters() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    _create_schema(engine)
    now = datetime.now(timezone.utc)
    failed_members = [
        CouncilMemberResult(
            provider="fake",
            model=f"broken-{index}",
            requested_model=f"broken-{index}",
            role=role,
            label=role.value,
            status="error",
            error_code="MODEL_UNAVAILABLE",
            error_message="Недоступно.",
        )
        for index, role in enumerate(
            [CouncilRole.ANALYST, CouncilRole.CRITIC]
        )
    ]
    error = CouncilExecutionError(
        "Все участники Совета ИИ завершились с ошибкой.",
        run_id="council_history_failed",
        started_at=now,
        finished_at=now + timedelta(milliseconds=200),
        duration_ms=200.0,
        member_results=failed_members,
    )

    with Session(engine) as session:
        session.add(WorkspaceModel(id="workspace_one", name="One"))
        history = CouncilHistoryService(
            repository=CouncilRunRepository(session),
            event_bus=EventBus(),
        )
        await history.record_failure(
            request=_request(),
            error=error,
        )
        session.commit()

        page = history.list_runs(
            workspace_id="workspace_one",
            status="failed",
            mode="code",
            query="архитектуры",
        )
        detail = history.get_run(
            run_id="council_history_failed",
            workspace_id="workspace_one",
        )

        assert page.total == 1
        assert page.items[0].successful_member_count == 0
        assert detail is not None
        assert detail.status == "failed"
        assert detail.synthesis is None
        assert detail.error_code == "COUNCIL_ALL_MEMBERS_FAILED"
        assert len(detail.members) == 2

        with pytest.raises(
            ValueError,
            match="started_from must not be after started_to",
        ):
            history.list_runs(
                workspace_id="workspace_one",
                started_from=now,
                started_to=now - timedelta(days=1),
            )


@pytest.mark.asyncio
async def test_retention_policy_redacts_content_and_purges_expired_runs() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    _create_schema(engine)
    now = datetime.now(timezone.utc)

    with Session(engine) as session:
        session.add(WorkspaceModel(id="workspace_one", name="One"))
        history = CouncilHistoryService(
            repository=CouncilRunRepository(session),
            settings_repository=SettingsRepository(session),
            event_bus=EventBus(),
        )
        policy = await history.update_retention_policy(
            workspace_id="workspace_one",
            update=CouncilRetentionPolicyUpdate(
                retention_days=1,
                auto_delete_enabled=False,
                store_member_answers=False,
                store_token_usage=False,
            ),
        )
        old_result = _success_result("council_expired").model_copy(
            update={
                "started_at": now - timedelta(days=3),
                "finished_at": now - timedelta(days=2),
            }
        )
        await history.record_success(
            request=_request(),
            result=old_result,
        )
        await history.record_success(
            request=_request(),
            result=_success_result("council_current"),
        )
        session.commit()

        current = history.get_run(
            run_id="council_current",
            workspace_id="workspace_one",
        )
        purge = await history.purge_expired(
            workspace_id="workspace_one"
        )
        session.commit()

        assert policy.retention_days == 1
        assert current is not None
        assert current.members[0].answer == ""
        assert current.members[0].usage.total_tokens is None
        assert purge.deleted_count == 1
        assert history.get_run(
            run_id="council_expired",
            workspace_id="workspace_one",
        ) is None
        assert history.list_runs(workspace_id="workspace_one").total == 1


@pytest.mark.asyncio
async def test_history_builds_failed_only_retry_plan() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    _create_schema(engine)
    successful = _success_result("council_partial")
    failed_member = CouncilMemberResult(
        provider="fake",
        model="model-b",
        requested_model="model-b",
        role=CouncilRole.CRITIC,
        label="Критик",
        status="error",
        error_code="RATE_LIMIT",
        error_message="Временный лимит.",
    )
    partial = successful.model_copy(
        update={
            "status": "partial",
            "members": [successful.members[0], failed_member],
        }
    )

    with Session(engine) as session:
        session.add(WorkspaceModel(id="workspace_one", name="One"))
        history = CouncilHistoryService(
            repository=CouncilRunRepository(session),
            event_bus=EventBus(),
        )
        await history.record_success(
            request=_request(),
            result=partial,
        )
        session.commit()

        plan = history.build_failed_retry_plan(
            run_id="council_partial",
            workspace_id="workspace_one",
        )
        hidden = history.build_failed_retry_plan(
            run_id="council_partial",
            workspace_id="workspace_two",
        )

        assert plan is not None
        assert plan.failed_member_indexes == (1,)
        assert list(plan.reused_member_results) == [0]
        assert plan.reused_member_results[0].answer == "Ответ аналитика."
        assert [member.model for member in plan.request.members] == [
            "model-a",
            "model-b",
        ]
        assert hidden is None


@pytest.mark.asyncio
async def test_history_persists_cancelled_run() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    _create_schema(engine)
    now = datetime.now(timezone.utc)
    request = CouncilRunRequest(
        question="Отменяем review после завершённого Reviewer.",
        execution_mode="review",
        members=_request().members,
        synthesizer_provider="fake",
        synthesizer_model="model-a",
        reviewer_provider="fake",
        reviewer_model="model-c",
        workspace_id="workspace_one",
    )
    cancelled_members = [
        CouncilMemberResult(
            provider=member.provider,
            model=member.model,
            requested_model=member.model,
            role=member.role,
            label=member.label or member.role.value,
            status="error",
            error_code="COUNCIL_MEMBER_CANCELLED",
            error_message="Запуск отменён.",
        )
        for member in request.members
    ]
    error = CouncilCancelledError(
        "Запуск Совета отменён пользователем.",
        run_id="council_cancelled",
        started_at=now,
        finished_at=now + timedelta(milliseconds=50),
        duration_ms=50,
        member_results=cancelled_members,
        auxiliary_results=[
            CouncilAuxiliaryResult(
                stage="reviewer",
                provider="fake",
                model="model-c",
                requested_model="model-c",
                label="Independent Reviewer",
                status="success",
                content="Проверка завершена до отмены.",
                usage=CouncilUsage(input_tokens=4, output_tokens=3, total_tokens=7),
                provider_reported_cost_usd=0.001,
            )
        ],
    )

    with Session(engine) as session:
        session.add(WorkspaceModel(id="workspace_one", name="One"))
        history = CouncilHistoryService(
            repository=CouncilRunRepository(session),
            event_bus=EventBus(),
        )
        await history.record_cancelled(
            request=request,
            error=error,
        )
        session.commit()

        detail = history.get_run(
            run_id="council_cancelled",
            workspace_id="workspace_one",
        )
        page = history.list_runs(
            workspace_id="workspace_one",
            status="cancelled",
        )

        assert detail is not None
        assert detail.status == "cancelled"
        assert detail.error_code == "COUNCIL_CANCELLED"
        assert detail.actual_cost_usd == pytest.approx(0.001)
        assert detail.actual_total_tokens == 7
        assert session.query(CouncilCostLedgerModel).filter_by(
            run_id="council_cancelled", kind="reviewer"
        ).count() == 1
        assert page.total == 1
