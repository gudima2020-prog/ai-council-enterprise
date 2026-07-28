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
from backend.task_engine.dependencies import (
    DependencyCreateRequest,
    TaskDependencyType,
)
from backend.task_engine.enums import TaskStatus, TaskType
from backend.task_engine.parallel_executor import ParallelTaskExecutor
from backend.task_engine.queue import TaskQueue
from backend.task_engine.repository import TaskRepository
from backend.task_engine.scheduler import TaskScheduler
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
async def test_hard_dependency_runs_in_dag_order() -> None:
    scope = make_scope()
    queue = TaskQueue()
    event_bus = EventBus()

    with scope() as session:
        repository = TaskRepository(session)
        first = repository.create(
            TaskCreate(
                task_type=TaskType.SYSTEM,
                title="First",
                payload={"action": "echo", "value": "first"},
            )
        )
        second = repository.create(
            TaskCreate(
                task_type=TaskType.SYSTEM,
                title="Second",
                payload={"action": "echo", "value": "second"},
            )
        )
        first_id = first.id
        second_id = second.id

    workflow = TaskWorkflowEngine(
        queue=queue,
        event_bus=event_bus,
        session_factory=scope,
    )
    await workflow.add_dependency(
        task_id=second_id,
        request=DependencyCreateRequest(
            depends_on_task_id=first_id,
        ),
    )

    executor = ParallelTaskExecutor(
        queue=queue,
        event_bus=event_bus,
        session_factory=scope,
        workflow_engine=workflow,
    )
    scheduler = TaskScheduler(
        queue=queue,
        event_bus=event_bus,
        handler=executor.execute_queue_item,
        worker_count=2,
    )

    await scheduler.start()
    result = await workflow.start_workflow(second_id)
    assert result is not None

    await asyncio.wait_for(queue.join(), timeout=2)
    await executor.shutdown()
    await scheduler.stop()

    with scope() as session:
        repository = TaskRepository(session)
        first = repository.get(first_id)
        second = repository.get(second_id)

        assert first is not None
        assert second is not None
        assert first.status == TaskStatus.COMPLETED.value
        assert second.status == TaskStatus.COMPLETED.value
        assert first.finished_at <= second.finished_at


@pytest.mark.asyncio
async def test_hard_failure_cascades_and_soft_dependency_continues() -> None:
    scope = make_scope()
    queue = TaskQueue()
    event_bus = EventBus()

    with scope() as session:
        repository = TaskRepository(session)
        upstream = repository.create(
            TaskCreate(
                task_type=TaskType.PLUGIN,
                title="Unsupported upstream",
            )
        )
        hard_child = repository.create(
            TaskCreate(
                task_type=TaskType.SYSTEM,
                title="Hard child",
                payload={"action": "echo", "value": "hard"},
            )
        )
        soft_child = repository.create(
            TaskCreate(
                task_type=TaskType.SYSTEM,
                title="Soft child",
                payload={"action": "echo", "value": "soft"},
            )
        )
        upstream_id = upstream.id
        hard_id = hard_child.id
        soft_id = soft_child.id

    workflow = TaskWorkflowEngine(
        queue=queue,
        event_bus=event_bus,
        session_factory=scope,
    )
    await workflow.add_dependency(
        task_id=hard_id,
        request=DependencyCreateRequest(
            depends_on_task_id=upstream_id,
        ),
    )
    await workflow.add_dependency(
        task_id=soft_id,
        request=DependencyCreateRequest(
            depends_on_task_id=upstream_id,
            dependency_type=TaskDependencyType.SOFT,
        ),
    )

    executor = ParallelTaskExecutor(
        queue=queue,
        event_bus=event_bus,
        session_factory=scope,
        workflow_engine=workflow,
    )
    scheduler = TaskScheduler(
        queue=queue,
        event_bus=event_bus,
        handler=executor.execute_queue_item,
        worker_count=2,
    )

    await scheduler.start()
    await workflow.start_workflow(upstream_id)
    await asyncio.wait_for(queue.join(), timeout=2)
    await executor.shutdown()
    await scheduler.stop()

    with scope() as session:
        repository = TaskRepository(session)
        upstream = repository.get(upstream_id)
        hard_child = repository.get(hard_id)
        soft_child = repository.get(soft_id)

        assert upstream is not None
        assert hard_child is not None
        assert soft_child is not None
        assert upstream.status == TaskStatus.FAILED.value
        assert hard_child.status == TaskStatus.FAILED.value
        assert soft_child.status == TaskStatus.COMPLETED.value
