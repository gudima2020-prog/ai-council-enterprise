from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.events import Event, EventBus
from backend.database import models as database_models  # noqa: F401
from backend.database.base import Base
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.orchestration.repository import ExecutionPlanRepository
from backend.orchestration.schemas import ExecutionPlanCreate
from backend.orchestration.service import ExecutionPlanService
from backend.orchestration.supervisor import ExecutionSupervisorService
from backend.orchestration.supervisor_schemas import (
    SupervisorActionType,
    SupervisorInterventionRequest,
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


class FakeRuntime:
    def __init__(self) -> None:
        self.cancelled: list[tuple[str, str]] = []

    async def cancel(self, plan_id: str, reason: str):
        self.cancelled.append((plan_id, reason))
        return {"plan_id": plan_id, "status": "cancelled"}


class FakePlanner:
    def __init__(self) -> None:
        self.replanned: list[str] = []

    async def replan(self, plan_id, request, *, automatic=False):
        self.replanned.append(plan_id)
        return {
            "source_plan": {"id": plan_id},
            "plan": {"id": "plan_replacement", "status": "ready"},
        }


@pytest.mark.asyncio
async def test_supervisor_records_failure_with_safe_default_policy() -> None:
    scope = make_scope()
    bus = EventBus()

    with scope() as session:
        plan = await ExecutionPlanService(
            ExecutionPlanRepository(session),
            bus,
        ).create_plan(
            ExecutionPlanCreate(
                title="Supervised plan",
                objective="Test failure monitoring.",
                steps=[],
            )
        )
        plan_id = plan["id"]

    service = ExecutionSupervisorService(
        event_bus=bus,
        runtime=FakeRuntime(),
        planner=FakePlanner(),
        session_factory=scope,
    )
    await service.start()
    try:
        policy = service.get_effective_policy(None)
        assert policy is not None
        assert policy["automatic_actions_enabled"] is False

        await service.handle_event(
            Event(
                event_type="execution_plan.runtime.failed",
                source="test",
                correlation_id=plan_id,
                payload={"plan_id": plan_id, "message": "boom"},
            )
        )
        await asyncio.sleep(0)

        incidents = service.list_incidents(plan_id=plan_id)
        assert len(incidents) == 1
        assert incidents[0]["incident_type"] == "plan_failed"
        assert incidents[0]["severity"] == "critical"

        actions = service.list_actions(plan_id=plan_id)
        assert len(actions) == 1
        assert actions[0]["action"] == "observe"
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_manual_cancel_intervention_is_persisted_and_executed() -> None:
    scope = make_scope()
    bus = EventBus()
    runtime = FakeRuntime()

    with scope() as session:
        plan = await ExecutionPlanService(
            ExecutionPlanRepository(session),
            bus,
        ).create_plan(
            ExecutionPlanCreate(
                title="Manual intervention",
                objective="Test a manual cancel action.",
                steps=[],
            )
        )
        plan_id = plan["id"]

    service = ExecutionSupervisorService(
        event_bus=bus,
        runtime=runtime,
        planner=FakePlanner(),
        session_factory=scope,
    )
    service._ensure_default_policy()

    action = await service.intervene(
        plan_id,
        SupervisorInterventionRequest(
            action=SupervisorActionType.CANCEL,
            reason="Operator stopped execution.",
            actor_id="owner",
            wait=True,
            idempotency_key="cancel-001",
        ),
    )

    assert action is not None
    assert action["status"] == "completed"
    assert runtime.cancelled == [(plan_id, "Operator stopped execution.")]

    duplicate = await service.intervene(
        plan_id,
        SupervisorInterventionRequest(
            action=SupervisorActionType.CANCEL,
            reason="Operator stopped execution.",
            actor_id="owner",
            wait=True,
            idempotency_key="cancel-001",
        ),
    )
    assert duplicate is not None
    assert duplicate["id"] == action["id"]
    assert len(runtime.cancelled) == 1
