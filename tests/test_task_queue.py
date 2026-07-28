from __future__ import annotations

import pytest

from backend.task_engine.queue import TaskQueue


@pytest.mark.asyncio
async def test_priority_and_fifo_order() -> None:
    queue = TaskQueue()

    await queue.put(task_id="normal-1", priority="normal")
    await queue.put(task_id="critical-1", priority="critical")
    await queue.put(task_id="normal-2", priority="normal")
    await queue.put(task_id="high-1", priority="high")

    items = [await queue.get() for _ in range(4)]

    assert [item.task_id for item in items] == [
        "critical-1",
        "high-1",
        "normal-1",
        "normal-2",
    ]


@pytest.mark.asyncio
async def test_duplicate_task_is_not_added() -> None:
    queue = TaskQueue()

    assert await queue.put(task_id="task-1", priority="normal") is True
    assert await queue.put(task_id="task-1", priority="normal") is False
    assert queue.qsize() == 1
