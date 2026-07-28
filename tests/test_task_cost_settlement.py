from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.events import EventBus
from backend.database.base import Base
from backend.database import models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401
from backend.task_engine.admission import TaskAdmissionManager
from backend.task_engine.budget_schemas import BudgetPolicyCreate
from backend.task_engine.enums import TaskStatus
from backend.task_engine.repository import TaskRepository
from backend.task_engine.schemas import TaskCreate
from backend.task_engine.state_machine import TaskStateMachine


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
async def test_completed_task_releases_reservation_and_charges_actual() -> None:
    scope = make_scope()
    manager = TaskAdmissionManager(
        event_bus=EventBus(),
        session_factory=scope,
    )
    await manager.create_policy(
        BudgetPolicyCreate(name="Budget", limit_usd=10)
    )

    with scope() as session:
        task = TaskRepository(session).create(
            TaskCreate(
                title="Costed task",
                payload={"_cost": {"estimated_usd": 3}},
            )
        )
        TaskStateMachine.transition(task, TaskStatus.QUEUED)
        task_id = task.id

    admitted = await manager.evaluate_task(task_id, reserve=True)
    assert admitted is not None and admitted["allowed"] is True

    with scope() as session:
        task = TaskRepository(session).get(task_id)
        assert task is not None
        TaskStateMachine.transition(task, TaskStatus.RUNNING)
        task.result_json = {"_usage": {"cost_usd": 2.5}}
        TaskStateMachine.transition(task, TaskStatus.COMPLETED)

    settled = await manager.settle_task(
        task_id,
        outcome="completed",
        reference_id="event-test-completed",
    )

    assert settled is not None
    assert settled["reserved_usd"] == 0.0
    assert settled["spent_usd"] == 2.5

    status = manager.budget_status()
    assert status["usage"]["spent_usd"] == 2.5
    assert status["usage"]["reserved_usd"] == 0.0


@pytest.mark.asyncio
async def test_failed_task_releases_without_charge() -> None:
    scope = make_scope()
    manager = TaskAdmissionManager(
        event_bus=EventBus(),
        session_factory=scope,
    )

    with scope() as session:
        task = TaskRepository(session).create(
            TaskCreate(
                title="Failed task",
                payload={"_cost": {"estimated_usd": 1.25}},
            )
        )
        TaskStateMachine.transition(task, TaskStatus.QUEUED)
        task_id = task.id

    await manager.evaluate_task(task_id, reserve=True)
    settled = await manager.settle_task(
        task_id,
        outcome="released",
        reference_id="event-test-failed",
    )

    assert settled is not None
    assert settled["reserved_usd"] == 0.0
    assert settled["spent_usd"] == 0.0
