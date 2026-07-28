from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.events import EventBus
from backend.database.base import Base
from backend.database import models as database_models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.orchestration.enums import (
    ExecutionPlanStatus,
    ExecutionStepStatus,
    ExecutionStepType,
)
from backend.orchestration.models import ExecutionPlanModel
from backend.orchestration.planner import ExecutionPlannerService
from backend.orchestration.planner_schemas import (
    ExecutionPlanGenerationRequest,
    ExecutionPlanReplanRequest,
    PlannerMode,
)
from backend.orchestration.repository import ExecutionPlanRepository


def make_scope():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
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
async def test_builtin_planner_generates_valid_thorough_plan() -> None:
    scope = make_scope()
    planner = ExecutionPlannerService(
        event_bus=EventBus(),
        session_factory=scope,
    )

    result = await planner.generate(
        ExecutionPlanGenerationRequest(
            title="Подготовить результат",
            objective="Проанализировать данные и подготовить отчёт.",
            mode=PlannerMode.THOROUGH,
            auto_assign=False,
            auto_validate=True,
        )
    )

    plan = result["plan"]
    run = result["planner_run"]

    assert plan["status"] == ExecutionPlanStatus.VALIDATED.value
    assert plan["validation"]["valid"] is True
    assert len(plan["steps"]) == 7
    assert {step["step_key"] for step in plan["steps"]} == {
        "analyze",
        "research",
        "risk",
        "design",
        "execute",
        "verify",
        "deliver",
    }
    assert plan["metadata"]["_planner"]["root_plan_id"] == plan["id"]
    assert run["status"] == "completed"
    assert run["result_plan_id"] == plan["id"]


@pytest.mark.asyncio
async def test_replan_preserves_completed_outputs_and_supersedes_source() -> None:
    scope = make_scope()
    planner = ExecutionPlannerService(
        event_bus=EventBus(),
        session_factory=scope,
    )

    generated = await planner.generate(
        ExecutionPlanGenerationRequest(
            objective="Выполнить цифровую задачу.",
            mode=PlannerMode.FAST,
            auto_assign=False,
            auto_validate=True,
        )
    )
    source_plan_id = generated["plan"]["id"]

    with scope() as session:
        repository = ExecutionPlanRepository(session)
        plan = repository.get(source_plan_id)
        assert plan is not None
        plan.status = ExecutionPlanStatus.FAILED.value

        analyze = repository.get_step_by_key(source_plan_id, "analyze")
        execute = repository.get_step_by_key(source_plan_id, "execute")
        deliver = repository.get_step_by_key(source_plan_id, "deliver")
        assert analyze is not None
        assert execute is not None
        assert deliver is not None

        analyze.status = ExecutionStepStatus.COMPLETED.value
        analyze.output_json = {"score": 92}
        execute.status = ExecutionStepStatus.FAILED.value
        execute.output_json = {"error": "temporary_failure"}
        deliver.status = ExecutionStepStatus.CANCELLED.value

        previous_timeout = execute.timeout_seconds
        previous_retries = execute.max_retries

    replanned = await planner.replan(
        source_plan_id,
        ExecutionPlanReplanRequest(
            reason="Исполнитель завершился с временной ошибкой.",
            preserve_completed_outputs=True,
            auto_assign=False,
            auto_validate=True,
        ),
    )
    assert replanned is not None

    new_plan = replanned["plan"]
    source_plan = replanned["source_plan"]
    by_key = {step["step_key"]: step for step in new_plan["steps"]}

    assert source_plan["status"] == ExecutionPlanStatus.SUPERSEDED.value
    assert new_plan["version"] == 2
    assert new_plan["metadata"]["_planner"]["parent_plan_id"] == source_plan_id
    assert new_plan["metadata"]["_planner"]["root_plan_id"] == source_plan_id
    assert by_key["analyze"]["step_type"] == ExecutionStepType.CHECKPOINT.value
    assert by_key["analyze"]["input"]["_preserved_output"] == {"score": 92}
    assert by_key["execute"]["timeout_seconds"] > previous_timeout
    assert by_key["execute"]["max_retries"] == previous_retries + 1
    assert replanned["planner_run"]["run_type"] == "replan"
    assert replanned["planner_run"]["status"] == "completed"


@pytest.mark.asyncio
async def test_replan_rejects_non_terminal_plan_without_force() -> None:
    scope = make_scope()
    planner = ExecutionPlannerService(
        event_bus=EventBus(),
        session_factory=scope,
    )

    generated = await planner.generate(
        ExecutionPlanGenerationRequest(
            objective="Проверить ограничение replanning.",
            mode=PlannerMode.FAST,
            auto_assign=False,
        )
    )

    with pytest.raises(Exception, match="Replanning разрешён"):
        await planner.replan(
            generated["plan"]["id"],
            ExecutionPlanReplanRequest(reason="Нет ошибки."),
        )
