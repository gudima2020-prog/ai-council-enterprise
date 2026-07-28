from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.events import EventBus
from backend.database import models as database_models  # noqa: F401
from backend.database.base import Base
from backend.orchestration.enums import ExecutionPlanStatus
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.orchestration.repository import ExecutionPlanRepository
from backend.orchestration.schemas import (
    ExecutionPlanCreate,
    ExecutionPlanStepCreate,
    ExecutionPlanStepUpdate,
    ExecutionPlanTransitionRequest,
)
from backend.orchestration.service import (
    ExecutionPlanError,
    ExecutionPlanService,
)
from backend.task_engine import models as task_models  # noqa: F401


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


def valid_plan_request() -> ExecutionPlanCreate:
    return ExecutionPlanCreate(
        title="Research and prepare report",
        objective="Collect data, analyze it and prepare outputs.",
        max_parallel_steps=1,
        planner="builtin.planner",
        steps=[
            ExecutionPlanStepCreate(
                step_key="collect",
                sequence=10,
                step_type="tool",
                title="Collect data",
                tool_name="web_search",
            ),
            ExecutionPlanStepCreate(
                step_key="analyze",
                sequence=20,
                step_type="agent",
                title="Analyze data",
                agent_role="analyst",
                capability="analysis",
                depends_on=["collect"],
            ),
            ExecutionPlanStepCreate(
                step_key="report",
                sequence=30,
                step_type="agent",
                title="Prepare report",
                agent_role="writer",
                depends_on=["analyze"],
            ),
            ExecutionPlanStepCreate(
                step_key="notify",
                sequence=31,
                step_type="tool",
                title="Notify user",
                tool_name="notification",
                depends_on=["analyze"],
            ),
        ],
    )


@pytest.mark.asyncio
async def test_plan_validation_and_state_transition() -> None:
    scope = make_scope()

    with scope() as session:
        service = ExecutionPlanService(
            ExecutionPlanRepository(session),
            EventBus(),
        )
        created = await service.create_plan(valid_plan_request())
        validated = await service.validate_plan(created["id"])

        assert validated is not None
        assert validated["status"] == ExecutionPlanStatus.VALIDATED.value
        assert validated["validation"]["valid"] is True
        assert validated["validation"]["topological_order"] == [
            "collect",
            "analyze",
            "report",
            "notify",
        ]
        assert validated["validation"]["parallel_groups"] == [
            ["collect"],
            ["analyze"],
            ["report", "notify"],
        ]
        assert validated["validation"]["max_width"] == 2
        assert validated["validation"]["warnings"]

        ready = await service.transition_plan(
            created["id"],
            ExecutionPlanTransitionRequest(status="ready"),
        )
        assert ready is not None
        assert ready["status"] == ExecutionPlanStatus.READY.value


@pytest.mark.asyncio
async def test_cycle_is_rejected_by_validation() -> None:
    scope = make_scope()

    request = ExecutionPlanCreate(
        title="Cyclic",
        objective="Must fail validation.",
        steps=[
            ExecutionPlanStepCreate(
                step_key="a",
                step_type="agent",
                title="A",
                agent_role="one",
                depends_on=["b"],
            ),
            ExecutionPlanStepCreate(
                step_key="b",
                step_type="agent",
                title="B",
                agent_role="two",
                depends_on=["a"],
            ),
        ],
    )

    with scope() as session:
        service = ExecutionPlanService(
            ExecutionPlanRepository(session),
            EventBus(),
        )
        created = await service.create_plan(request)
        validated = await service.validate_plan(created["id"])

        assert validated is not None
        assert validated["status"] == ExecutionPlanStatus.DRAFT.value
        assert validated["validation"]["valid"] is False
        assert any(
            "цикл" in error.lower()
            for error in validated["validation"]["errors"]
        )

        with pytest.raises(ExecutionPlanError):
            await service.transition_plan(
                created["id"],
                ExecutionPlanTransitionRequest(status="ready"),
            )


@pytest.mark.asyncio
async def test_editing_validated_plan_resets_validation() -> None:
    scope = make_scope()

    with scope() as session:
        service = ExecutionPlanService(
            ExecutionPlanRepository(session),
            EventBus(),
        )
        created = await service.create_plan(valid_plan_request())
        validated = await service.validate_plan(created["id"])
        assert validated is not None

        report = next(
            step
            for step in validated["steps"]
            if step["step_key"] == "report"
        )
        updated = await service.update_step(
            created["id"],
            report["id"],
            ExecutionPlanStepUpdate(title="Prepare final report"),
        )

        assert updated is not None
        assert updated["status"] == ExecutionPlanStatus.DRAFT.value
        assert updated["validation"] == {}


@pytest.mark.asyncio
async def test_dependent_step_cannot_be_deleted() -> None:
    scope = make_scope()

    with scope() as session:
        service = ExecutionPlanService(
            ExecutionPlanRepository(session),
            EventBus(),
        )
        created = await service.create_plan(valid_plan_request())
        collect = next(
            step
            for step in created["steps"]
            if step["step_key"] == "collect"
        )

        with pytest.raises(ExecutionPlanError):
            await service.delete_step(created["id"], collect["id"])
