from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.events import EventBus
from backend.database.base import Base
from backend.database import models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401
from backend.task_engine.enums import TaskStatus, TaskType
from backend.task_engine.executor import TaskExecutor
from backend.task_engine.queue import QueueItem, TaskQueue
from backend.task_engine.repository import TaskRepository
from backend.task_engine.schemas import TaskCreate
from backend.task_engine.state_machine import TaskStateMachine


def make_session_factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
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
async def test_timeout_marks_task_failed() -> None:
    scope = make_session_factory()

    with scope() as session:
        repository = TaskRepository(session)
        task = repository.create(
            TaskCreate(
                task_type=TaskType.SYSTEM,
                title="Timeout",
                payload={"action": "sleep", "seconds": 2},
                timeout_seconds=1,
                max_retries=0,
            )
        )
        TaskStateMachine.transition(task, TaskStatus.QUEUED)
        task_id = task.id

    executor = TaskExecutor(
        queue=TaskQueue(),
        event_bus=EventBus(),
        session_factory=scope,
    )

    await executor.execute_queue_item(
        QueueItem(
            priority_weight=30,
            sequence=0,
            task_id=task_id,
        )
    )

    with scope() as session:
        full = TaskRepository(session).get_full(task_id)

        assert full is not None
        assert full.status == TaskStatus.FAILED.value
        assert "timeout" in full.runs[0].error.lower()


@pytest.mark.asyncio
async def test_automatic_retry_is_requeued() -> None:
    scope = make_session_factory()
    queue = TaskQueue()

    with scope() as session:
        repository = TaskRepository(session)
        task = repository.create(
            TaskCreate(
                task_type=TaskType.PLUGIN,
                title="Retry",
                max_retries=1,
                retry_delay_seconds=0,
            )
        )
        TaskStateMachine.transition(task, TaskStatus.QUEUED)
        task_id = task.id

    executor = TaskExecutor(
        queue=queue,
        event_bus=EventBus(),
        session_factory=scope,
    )

    async def failing_handler(context):
        raise RuntimeError("temporary")

    executor.register(
        TaskType.PLUGIN.value,
        failing_handler,
    )

    await executor.execute_queue_item(
        QueueItem(
            priority_weight=30,
            sequence=0,
            task_id=task_id,
        )
    )

    item = await asyncio.wait_for(queue.get(), timeout=1)
    assert item.task_id == task_id

    with scope() as session:
        task = TaskRepository(session).get(task_id)

        assert task is not None
        assert task.status == TaskStatus.QUEUED.value
        assert task.retry_count == 1

    await executor.shutdown()


@pytest.mark.asyncio
async def test_running_task_can_be_cancelled() -> None:
    scope = make_session_factory()
    started = asyncio.Event()

    with scope() as session:
        repository = TaskRepository(session)
        task = repository.create(
            TaskCreate(
                task_type=TaskType.PLUGIN,
                title="Cancel me",
                timeout_seconds=30,
            )
        )
        TaskStateMachine.transition(task, TaskStatus.QUEUED)
        task_id = task.id

    executor = TaskExecutor(
        queue=TaskQueue(),
        event_bus=EventBus(),
        session_factory=scope,
    )

    async def slow_handler(context):
        started.set()
        await asyncio.sleep(30)
        return {"unexpected": True}

    executor.register(
        TaskType.PLUGIN.value,
        slow_handler,
    )

    execution = asyncio.create_task(
        executor.execute_queue_item(
            QueueItem(
                priority_weight=30,
                sequence=0,
                task_id=task_id,
            )
        )
    )

    await asyncio.wait_for(started.wait(), timeout=1)
    result = await executor.request_cancel(task_id, "User cancelled.")
    await asyncio.wait_for(execution, timeout=1)

    assert result is not None
    assert result["cancellation_requested"] is True

    with scope() as session:
        full = TaskRepository(session).get_full(task_id)

        assert full is not None
        assert full.status == TaskStatus.CANCELLED.value
        assert full.cancel_reason == "User cancelled."
        assert full.runs[0].status == TaskStatus.CANCELLED.value
