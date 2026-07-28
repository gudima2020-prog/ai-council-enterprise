from __future__ import annotations

import asyncio

import pytest

from backend.core.events import EventBus
from backend.task_engine.queue import TaskQueue
from backend.task_engine.worker_pool import TaskWorkerPool


@pytest.mark.asyncio
async def test_worker_pool_respects_global_concurrency() -> None:
    queue = TaskQueue()
    active = 0
    maximum = 0

    async def handler(item) -> None:
        nonlocal active, maximum

        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.04)
        active -= 1

    pool = TaskWorkerPool(
        queue=queue,
        event_bus=EventBus(),
        handler=handler,
        worker_count=2,
    )

    for index in range(6):
        await queue.put(
            task_id=f"task-{index}",
            priority="normal",
        )

    await pool.start()
    await asyncio.wait_for(queue.join(), timeout=2)
    await pool.stop()

    assert maximum == 2
    assert pool.stats()["processed"] == 6


@pytest.mark.asyncio
async def test_worker_pool_processes_tasks_in_parallel() -> None:
    queue = TaskQueue()
    started: set[str] = set()
    release = asyncio.Event()

    async def handler(item) -> None:
        started.add(item.task_id)

        if len(started) == 3:
            release.set()

        await release.wait()

    pool = TaskWorkerPool(
        queue=queue,
        event_bus=EventBus(),
        handler=handler,
        worker_count=3,
    )

    for index in range(3):
        await queue.put(
            task_id=f"task-{index}",
            priority="normal",
        )

    await pool.start()
    await asyncio.wait_for(release.wait(), timeout=1)
    await asyncio.wait_for(queue.join(), timeout=1)
    await pool.stop()

    assert len(started) == 3
