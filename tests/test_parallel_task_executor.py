from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.events import EventBus
from backend.database.base import Base
from backend.database import models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401
from backend.task_engine.enums import TaskStatus, TaskType
from backend.task_engine.parallel_executor import ParallelTaskExecutor
from backend.task_engine.queue import TaskQueue
from backend.task_engine.repository import TaskRepository
from backend.task_engine.scheduler import TaskScheduler
from backend.task_engine.schemas import TaskCreate
from backend.task_engine.state_machine import TaskStateMachine


def make_session_factory():
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
async def test_shared_resource_serializes_parallel_tasks() -> None:
    scope = make_session_factory()
    queue = TaskQueue()
    active = 0
    maximum = 0

    with scope() as session:
        repository = TaskRepository(session)
        task_ids: list[str] = []

        for index in range(2):
            task = repository.create(
                TaskCreate(
                    task_type=TaskType.PLUGIN,
                    title=f"Account task {index}",
                    payload={
                        "_execution": {
                            "resource_locks": [
                                "exchange:bingx:main-account"
                            ]
                        }
                    },
                )
            )
            TaskStateMachine.transition(task, TaskStatus.QUEUED)
            task_ids.append(task.id)

    executor = ParallelTaskExecutor(
        queue=queue,
        event_bus=EventBus(),
        session_factory=scope,
        type_limits={TaskType.PLUGIN.value: 2},
    )

    async def handler(context):
        nonlocal active, maximum

        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.04)
        active -= 1
        return {"ok": True}

    executor.register(TaskType.PLUGIN.value, handler)

    scheduler = TaskScheduler(
        queue=queue,
        event_bus=EventBus(),
        handler=executor.execute_queue_item,
        worker_count=2,
    )

    await scheduler.start()

    for task_id in task_ids:
        await scheduler.enqueue(
            task_id=task_id,
            priority="normal",
        )

    await asyncio.wait_for(queue.join(), timeout=2)
    await executor.shutdown()
    await scheduler.stop()

    assert maximum == 1

    with scope() as session:
        repository = TaskRepository(session)

        assert all(
            repository.get(task_id).status == TaskStatus.COMPLETED.value
            for task_id in task_ids
        )


@pytest.mark.asyncio
async def test_different_resources_can_run_in_parallel() -> None:
    scope = make_session_factory()
    queue = TaskQueue()
    active = 0
    maximum = 0

    with scope() as session:
        repository = TaskRepository(session)
        task_ids: list[str] = []

        for index, resource in enumerate(("account:a", "account:b")):
            task = repository.create(
                TaskCreate(
                    task_type=TaskType.PLUGIN,
                    title=f"Independent task {index}",
                    payload={
                        "_execution": {
                            "resource_locks": [resource]
                        }
                    },
                )
            )
            TaskStateMachine.transition(task, TaskStatus.QUEUED)
            task_ids.append(task.id)

    executor = ParallelTaskExecutor(
        queue=queue,
        event_bus=EventBus(),
        session_factory=scope,
        type_limits={TaskType.PLUGIN.value: 2},
    )

    async def handler(context):
        nonlocal active, maximum

        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.04)
        active -= 1
        return {"ok": True}

    executor.register(TaskType.PLUGIN.value, handler)

    scheduler = TaskScheduler(
        queue=queue,
        event_bus=EventBus(),
        handler=executor.execute_queue_item,
        worker_count=2,
    )

    await scheduler.start()

    for task_id in task_ids:
        await scheduler.enqueue(
            task_id=task_id,
            priority="normal",
        )

    await asyncio.wait_for(queue.join(), timeout=2)
    await executor.shutdown()
    await scheduler.stop()

    assert maximum == 2
