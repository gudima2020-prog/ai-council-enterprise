from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.events import Event, EventBus
from backend.database.base import Base
from backend.database import models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401
from backend.task_engine.dead_letter import (
    DeadLetterError,
    TaskDeadLetterManager,
)
from backend.task_engine.dead_letter_schemas import (
    DeadLetterReplayRequest,
)
from backend.task_engine.enums import TaskStatus, TaskType
from backend.task_engine.queue import TaskQueue
from backend.task_engine.repository import TaskRepository
from backend.task_engine.schemas import TaskCreate, TaskLogCreate
from backend.task_engine.state_machine import TaskStateMachine
from backend.task_engine.workflow import TaskWorkflowEngine


def make_runtime():
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

    queue = TaskQueue()
    event_bus = EventBus()
    workflow = TaskWorkflowEngine(
        queue=queue,
        event_bus=event_bus,
        session_factory=scope,
    )
    manager = TaskDeadLetterManager(
        event_bus=event_bus,
        queue=queue,
        workflow_engine=workflow,
        session_factory=scope,
        max_replays_per_entry=2,
    )
    return scope, queue, event_bus, manager


@pytest.mark.asyncio
async def test_final_failure_is_captured_once() -> None:
    scope, _, _, manager = make_runtime()

    with scope() as session:
        repository = TaskRepository(session)
        task = repository.create(
            TaskCreate(
                task_type=TaskType.PLUGIN,
                title="Poison task",
                payload={"value": 1},
            )
        )
        TaskStateMachine.transition(task, TaskStatus.FAILED)
        repository.append_log(
            task.id,
            TaskLogCreate(
                level="ERROR",
                message="boom",
                metadata={"error_type": "RuntimeError"},
            ),
        )
        task_id = task.id

    event = Event(
        event_type="task.executor.failed",
        source="test",
        payload={
            "task_id": task_id,
            "error_type": "RuntimeError",
            "message": "boom",
            "retry_scheduled": False,
        },
    )

    first = await manager.capture_from_event(event)
    second = await manager.capture_from_event(event)

    assert first is not None and first["captured"] is True
    assert second is not None and second["duplicate"] is True
    assert manager.stats()["entries"]["open"] == 1


@pytest.mark.asyncio
async def test_retrying_task_is_not_captured() -> None:
    scope, _, _, manager = make_runtime()

    with scope() as session:
        repository = TaskRepository(session)
        task = repository.create(
            TaskCreate(title="Retry pending", max_retries=1)
        )
        TaskStateMachine.transition(task, TaskStatus.FAILED)
        TaskStateMachine.transition(task, TaskStatus.RETRYING)
        task_id = task.id

    result = await manager.capture_from_event(
        Event(
            event_type="task.executor.failed",
            source="test",
            payload={"task_id": task_id},
        )
    )

    assert result is not None
    assert result["captured"] is False
    assert result["reason"] == "task_not_finally_failed"
    assert manager.list_entries() == []


@pytest.mark.asyncio
async def test_replay_clones_task_and_is_idempotent() -> None:
    scope, queue, _, manager = make_runtime()

    with scope() as session:
        repository = TaskRepository(session)
        task = repository.create(
            TaskCreate(
                task_type=TaskType.SYSTEM,
                title="Replay source",
                payload={
                    "action": "echo",
                    "nested": {"a": 1, "b": 2},
                },
                max_retries=1,
            )
        )
        TaskStateMachine.transition(task, TaskStatus.FAILED)
        task_id = task.id

    captured = await manager.capture_task(
        task_id=task_id,
        reason_code="test_failure",
        error_message="temporary",
    )
    assert captured is not None

    request = DeadLetterReplayRequest(
        actor_id="owner",
        reason="Verified and approved replay.",
        idempotency_key="replay-001",
        payload_patch={"nested": {"b": 3}, "new": True},
    )
    replay = await manager.replay(captured["id"], request)
    duplicate = await manager.replay(captured["id"], request)

    assert replay is not None
    assert replay["status"] == "enqueued"
    assert duplicate is not None
    assert duplicate["idempotent"] is True
    assert duplicate["replay_task_id"] == replay["replay_task_id"]
    assert queue.qsize() == 1

    with scope() as session:
        cloned = TaskRepository(session).get(replay["replay_task_id"])
        assert cloned is not None
        assert cloned.id != task_id
        assert cloned.status == TaskStatus.QUEUED.value
        assert cloned.payload_json["nested"] == {"a": 1, "b": 3}
        assert cloned.payload_json["new"] is True
        assert cloned.payload_json["_replay"]["source_task_id"] == task_id


@pytest.mark.asyncio
async def test_successful_replay_resolves_entry() -> None:
    scope, _, _, manager = make_runtime()

    with scope() as session:
        repository = TaskRepository(session)
        task = repository.create(TaskCreate(title="Source"))
        TaskStateMachine.transition(task, TaskStatus.FAILED)
        task_id = task.id

    captured = await manager.capture_task(
        task_id=task_id,
        reason_code="test",
    )
    assert captured is not None

    replay = await manager.replay(
        captured["id"],
        DeadLetterReplayRequest(
            actor_id="owner",
            reason="Approved.",
        ),
    )
    assert replay is not None

    with scope() as session:
        task = TaskRepository(session).get(replay["replay_task_id"])
        assert task is not None
        TaskStateMachine.transition(task, TaskStatus.RUNNING)
        TaskStateMachine.transition(task, TaskStatus.COMPLETED)

    terminal = await manager.handle_replay_terminal_event(
        Event(
            event_type="task.executor.completed",
            source="test",
            payload={"task_id": replay["replay_task_id"]},
        )
    )

    assert terminal is not None
    assert terminal["status"] == "completed"
    entry = manager.get_entry(captured["id"])
    assert entry is not None
    assert entry["status"] == "resolved"


@pytest.mark.asyncio
async def test_replay_limit_requires_force() -> None:
    scope, _, _, manager = make_runtime()

    with scope() as session:
        repository = TaskRepository(session)
        task = repository.create(TaskCreate(title="Limited"))
        TaskStateMachine.transition(task, TaskStatus.FAILED)
        task_id = task.id

    captured = await manager.capture_task(
        task_id=task_id,
        reason_code="test",
    )
    assert captured is not None

    for index in range(2):
        await manager.replay(
            captured["id"],
            DeadLetterReplayRequest(
                actor_id="owner",
                reason=f"Replay {index}",
                idempotency_key=f"key-{index}",
            ),
        )

    with pytest.raises(DeadLetterError):
        await manager.replay(
            captured["id"],
            DeadLetterReplayRequest(
                actor_id="owner",
                reason="Third replay",
            ),
        )
