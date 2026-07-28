from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.core.events import EventBus
from backend.database.base import Base
from backend.database import models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401
from backend.task_engine.enums import TaskStatus
from backend.task_engine.repository import TaskRepository
from backend.task_engine.schemas import (
    TaskCreate,
    TaskTransitionRequest,
)
from backend.task_engine.service import TaskService


@pytest.mark.asyncio
async def test_transition_creates_log_and_event() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    received = []
    event_bus = EventBus()

    async def handler(event):
        received.append(event)

    event_bus.subscribe("task.queued", handler)

    with Session(engine) as session:
        service = TaskService(
            TaskRepository(session),
            event_bus,
        )

        created = await service.create_task(
            TaskCreate(
                title="Queue me",
                max_retries=2,
            )
        )

        transitioned = await service.transition_task(
            created["id"],
            TaskTransitionRequest(
                status=TaskStatus.QUEUED,
                reason="ready",
            ),
        )
        session.commit()

        full = service.get_task(created["id"])

    assert transitioned is not None
    assert transitioned["status"] == "queued"
    assert full is not None
    assert len(full["logs"]) == 1
    assert len(received) == 1
    assert received[0].event_type == "task.queued"
