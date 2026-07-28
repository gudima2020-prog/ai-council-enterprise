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
from backend.task_engine.budget_schemas import (
    BudgetEnforcementMode,
    BudgetPeriod,
    BudgetPolicyCreate,
)
from backend.task_engine.enums import TaskStatus, TaskType
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
async def test_hard_budget_denies_expensive_task() -> None:
    scope = make_scope()
    manager = TaskAdmissionManager(
        event_bus=EventBus(),
        session_factory=scope,
    )
    await manager.create_policy(
        BudgetPolicyCreate(
            name="Global daily",
            period=BudgetPeriod.DAILY,
            enforcement_mode=BudgetEnforcementMode.HARD,
            limit_usd=5,
            max_task_cost_usd=4,
        )
    )

    with scope() as session:
        task = TaskRepository(session).create(
            TaskCreate(
                task_type=TaskType.SYSTEM,
                title="Expensive",
                payload={"_cost": {"estimated_usd": 6}},
            )
        )
        TaskStateMachine.transition(task, TaskStatus.QUEUED)
        task_id = task.id

    decision = await manager.evaluate_task(
        task_id,
        reserve=True,
        source="test",
    )

    assert decision is not None
    assert decision["allowed"] is False
    assert decision["decision"] == "denied"
    assert len(decision["violations"]) >= 1

    with scope() as session:
        task = TaskRepository(session).get(task_id)
        assert task is not None
        assert task.status == TaskStatus.WAITING.value


@pytest.mark.asyncio
async def test_observe_policy_allows_and_records_reservation() -> None:
    scope = make_scope()
    manager = TaskAdmissionManager(
        event_bus=EventBus(),
        session_factory=scope,
    )
    await manager.create_policy(
        BudgetPolicyCreate(
            name="Observe",
            period=BudgetPeriod.LIFETIME,
            enforcement_mode=BudgetEnforcementMode.OBSERVE,
            limit_usd=1,
        )
    )

    with scope() as session:
        task = TaskRepository(session).create(
            TaskCreate(
                task_type=TaskType.SYSTEM,
                title="Observed",
                payload={"_cost": {"estimated_usd": 2}},
            )
        )
        TaskStateMachine.transition(task, TaskStatus.QUEUED)
        task_id = task.id

    decision = await manager.evaluate_task(
        task_id,
        reserve=True,
        source="test",
    )
    cost = manager.cost_status(task_id)

    assert decision is not None
    assert decision["allowed"] is True
    assert decision["decision"] == "observed"
    assert decision["warnings"]
    assert cost is not None
    assert cost["reserved_usd"] == 2.0


@pytest.mark.asyncio
async def test_override_allows_hard_denial() -> None:
    scope = make_scope()
    manager = TaskAdmissionManager(
        event_bus=EventBus(),
        session_factory=scope,
    )
    await manager.create_policy(
        BudgetPolicyCreate(
            name="Hard",
            limit_usd=1,
            enforcement_mode=BudgetEnforcementMode.HARD,
        )
    )

    with scope() as session:
        task = TaskRepository(session).create(
            TaskCreate(
                title="Override",
                payload={"_cost": {"estimated_usd": 3}},
            )
        )
        TaskStateMachine.transition(task, TaskStatus.QUEUED)
        task_id = task.id

    denied = await manager.evaluate_task(task_id, reserve=False)
    assert denied is not None and denied["allowed"] is False

    await manager.override_task(
        task_id,
        actor_id="owner",
        reason="Approved exceptional expense.",
    )
    allowed = await manager.evaluate_task(task_id, reserve=True)

    assert allowed is not None
    assert allowed["allowed"] is True
    assert allowed["decision"] == "overridden"
