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
from backend.task_engine.dependencies import (
    DependencyCreateRequest,
    DependencyCycleError,
)
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
async def test_dag_cycle_is_rejected() -> None:
    scope = make_scope()

    with scope() as session:
        repository = TaskRepository(session)
        first = repository.create(TaskCreate(title="First"))
        second = repository.create(TaskCreate(title="Second"))
        third = repository.create(TaskCreate(title="Third"))
        ids = first.id, second.id, third.id

    workflow = TaskWorkflowEngine(
        queue=TaskQueue(),
        event_bus=EventBus(),
        session_factory=scope,
    )

    await workflow.add_dependency(
        task_id=ids[1],
        request=DependencyCreateRequest(
            depends_on_task_id=ids[0],
        ),
    )
    await workflow.add_dependency(
        task_id=ids[2],
        request=DependencyCreateRequest(
            depends_on_task_id=ids[1],
        ),
    )

    with pytest.raises(DependencyCycleError):
        await workflow.add_dependency(
            task_id=ids[0],
            request=DependencyCreateRequest(
                depends_on_task_id=ids[2],
            ),
        )

    graph = workflow.graph(ids[2])
    assert graph is not None
    assert graph["valid"] is True
    order = graph["topological_order"]
    assert order.index(ids[0]) < order.index(ids[1])
    assert order.index(ids[1]) < order.index(ids[2])
