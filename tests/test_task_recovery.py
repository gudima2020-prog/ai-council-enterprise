from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.events import EventBus
from backend.database.base import Base
from backend.database import models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401
from backend.task_engine.enums import TaskStatus
from backend.task_engine.executor import TaskExecutor
from backend.task_engine.queue import TaskQueue
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
async def test_recovery_requeues_queued_and_fails_interrupted() -> None:
    scope = make_session_factory()

    with scope() as session:
        repository = TaskRepository(session)

        queued = repository.create(
            TaskCreate(title="Queued")
        )
        TaskStateMachine.transition(queued, TaskStatus.QUEUED)

        interrupted = repository.create(
            TaskCreate(title="Interrupted")
        )
        TaskStateMachine.transition(interrupted, TaskStatus.QUEUED)
        TaskStateMachine.transition(interrupted, TaskStatus.RUNNING)

        queued_id = queued.id
        interrupted_id = interrupted.id

    queue = TaskQueue()
    executor = TaskExecutor(
        queue=queue,
        event_bus=EventBus(),
        session_factory=scope,
    )

    result = await executor.recover_persistent_queue()

    assert result["enqueued"] == 1
    assert result["interrupted_failed"] == 1
    assert queue.qsize() == 1

    item = await queue.get()
    assert item.task_id == queued_id

    with scope() as session:
        repository = TaskRepository(session)
        interrupted = repository.get(interrupted_id)

        assert interrupted is not None
        assert interrupted.status == TaskStatus.FAILED.value
