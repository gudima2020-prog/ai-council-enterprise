from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

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
async def test_executor_completes_system_health_check() -> None:
    scope = make_session_factory()

    with scope() as session:
        repository = TaskRepository(session)
        task = repository.create(
            TaskCreate(
                task_type=TaskType.SYSTEM,
                title="Health",
                payload={"action": "health_check"},
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
        assert full.status == TaskStatus.COMPLETED.value
        assert full.result_json["status"] == "ok"
        assert len(full.runs) == 1
        assert full.runs[0].status == TaskStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_executor_fails_unknown_handler() -> None:
    scope = make_session_factory()

    with scope() as session:
        repository = TaskRepository(session)
        task = repository.create(
            TaskCreate(
                task_type=TaskType.PLUGIN,
                title="Unknown plugin task",
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
        assert len(full.runs) == 1
        assert full.runs[0].error is not None
