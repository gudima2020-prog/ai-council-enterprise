from __future__ import annotations

import asyncio

import pytest

from backend.core.events import EventBus
from backend.task_engine.queue import TaskQueue
from backend.task_engine.scheduler import TaskScheduler


@pytest.mark.asyncio
async def test_scheduler_processes_enqueued_task() -> None:
    processed: list[str] = []
    event_bus = EventBus()

    async def handler(item) -> None:
        processed.append(item.task_id)

    scheduler = TaskScheduler(
        queue=TaskQueue(),
        event_bus=event_bus,
        handler=handler,
    )

    await scheduler.start()
    await scheduler.enqueue(task_id="task-1", priority="high")

    await asyncio.wait_for(scheduler._queue.join(), timeout=1)
    await scheduler.stop()

    assert processed == ["task-1"]
    assert scheduler.stats()["processed"] == 1
    assert scheduler.stats()["running"] is False


@pytest.mark.asyncio
async def test_scheduler_emits_failure_without_stopping() -> None:
    event_bus = EventBus()
    received = []

    async def on_failure(event) -> None:
        received.append(event)

    async def failing_handler(item) -> None:
        raise RuntimeError("boom")

    event_bus.subscribe("task.scheduler.failed", on_failure)

    scheduler = TaskScheduler(
        queue=TaskQueue(),
        event_bus=event_bus,
        handler=failing_handler,
    )

    await scheduler.start()
    await scheduler.enqueue(task_id="task-fail", priority="normal")

    await asyncio.wait_for(scheduler._queue.join(), timeout=1)
    await scheduler.stop()

    assert scheduler.stats()["failed"] == 1
    assert len(received) == 1
