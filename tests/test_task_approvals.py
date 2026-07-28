from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.events import EventBus
from backend.database.base import Base
from backend.database import models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401
from backend.task_engine.approval_schemas import (
    ApprovalDecision,
    ApprovalDecisionRequest,
    ApprovalStatus,
    TaskApprovalRequest,
)
from backend.task_engine.approvals import (
    TaskApprovalManager,
    utc_now,
)
from backend.task_engine.enums import TaskStatus
from backend.task_engine.models import TaskApprovalModel
from backend.task_engine.queue import TaskQueue
from backend.task_engine.repository import TaskRepository
from backend.task_engine.schemas import TaskCreate
from backend.task_engine.workflow import TaskWorkflowEngine


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
async def test_manual_approval_blocks_and_then_resumes_task() -> None:
    scope = make_scope()
    queue = TaskQueue()
    event_bus = EventBus()

    with scope() as session:
        task = TaskRepository(session).create(
            TaskCreate(
                title="Require confirmation",
                payload={"action": "echo", "value": "approved"},
            )
        )
        task_id = task.id

    workflow = TaskWorkflowEngine(
        queue=queue,
        event_bus=event_bus,
        session_factory=scope,
    )
    manager = TaskApprovalManager(
        event_bus=event_bus,
        workflow_engine=workflow,
        session_factory=scope,
    )

    approval = await manager.request(
        task_id=task_id,
        request=TaskApprovalRequest(
            prompt="Разрешить выполнение?",
            requested_by="ai",
        ),
    )

    assert approval is not None
    assert approval["status"] == ApprovalStatus.PENDING.value

    blocked = await workflow.enqueue_task(
        task_id,
        source="test_before_approval",
    )

    assert blocked is not None
    assert blocked["approval_pending"] is True
    assert queue.qsize() == 0

    decision = await manager.decide(
        approval_id=approval["id"],
        request=ApprovalDecisionRequest(
            decision=ApprovalDecision.APPROVE,
            decided_by="user",
            note="Подтверждаю.",
        ),
    )

    assert decision is not None
    assert decision["status"] == ApprovalStatus.APPROVED.value
    assert decision["resume"]["enqueued"] is True
    assert queue.qsize() == 1

    with scope() as session:
        task = TaskRepository(session).get(task_id)

        assert task is not None
        assert task.status == TaskStatus.QUEUED.value


@pytest.mark.asyncio
async def test_rejected_approval_cancels_task() -> None:
    scope = make_scope()
    queue = TaskQueue()
    event_bus = EventBus()

    with scope() as session:
        task = TaskRepository(session).create(
            TaskCreate(title="Reject me")
        )
        task_id = task.id

    workflow = TaskWorkflowEngine(
        queue=queue,
        event_bus=event_bus,
        session_factory=scope,
    )
    manager = TaskApprovalManager(
        event_bus=event_bus,
        workflow_engine=workflow,
        session_factory=scope,
    )

    approval = await manager.request(
        task_id=task_id,
        request=TaskApprovalRequest(
            prompt="Разрешить?",
            requested_by="ai",
        ),
    )
    assert approval is not None

    result = await manager.decide(
        approval_id=approval["id"],
        request=ApprovalDecisionRequest(
            decision=ApprovalDecision.REJECT,
            decided_by="user",
            note="Не выполнять.",
        ),
    )

    assert result is not None
    assert result["status"] == ApprovalStatus.REJECTED.value
    assert queue.qsize() == 0

    with scope() as session:
        task = TaskRepository(session).get(task_id)

        assert task is not None
        assert task.status == TaskStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_expired_approval_is_reconciled() -> None:
    scope = make_scope()
    queue = TaskQueue()
    event_bus = EventBus()

    with scope() as session:
        task = TaskRepository(session).create(
            TaskCreate(title="Expire me")
        )
        task_id = task.id

    workflow = TaskWorkflowEngine(
        queue=queue,
        event_bus=event_bus,
        session_factory=scope,
    )
    manager = TaskApprovalManager(
        event_bus=event_bus,
        workflow_engine=workflow,
        session_factory=scope,
    )

    approval = await manager.request(
        task_id=task_id,
        request=TaskApprovalRequest(
            prompt="Temporary approval",
            expires_in_seconds=60,
        ),
    )
    assert approval is not None

    with scope() as session:
        row = session.get(TaskApprovalModel, approval["id"])
        assert row is not None
        row.expires_at = utc_now() - timedelta(seconds=1)

    result = await manager.reconcile_expired()

    assert result["expired_count"] == 1

    with scope() as session:
        task = TaskRepository(session).get(task_id)
        row = session.get(TaskApprovalModel, approval["id"])

        assert task is not None
        assert row is not None
        assert task.status == TaskStatus.CANCELLED.value
        assert row.status == ApprovalStatus.EXPIRED.value
