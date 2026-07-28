from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from itertools import count
from typing import Any

from backend.task_engine.enums import TaskPriority


_PRIORITY_WEIGHT = {
    TaskPriority.CRITICAL.value: 0,
    TaskPriority.URGENT.value: 10,
    TaskPriority.HIGH.value: 20,
    TaskPriority.NORMAL.value: 30,
    TaskPriority.LOW.value: 40,
}


@dataclass(order=True)
class QueueItem:
    sort_key: tuple[int, int] = field(init=False, repr=False)
    priority_weight: int
    sequence: int
    task_id: str = field(compare=False)
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        self.sort_key = (self.priority_weight, self.sequence)


class TaskQueue:
    """
    Async in-memory priority queue.

    Lower weight means higher priority. FIFO ordering is preserved between
    tasks with the same priority through a monotonically increasing sequence.
    """

    def __init__(self) -> None:
        self._queue: asyncio.PriorityQueue[QueueItem] = asyncio.PriorityQueue()
        self._sequence = count()
        self._queued_ids: set[str] = set()
        self._lock = asyncio.Lock()

    async def put(
        self,
        *,
        task_id: str,
        priority: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        async with self._lock:
            if task_id in self._queued_ids:
                return False

            item = QueueItem(
                priority_weight=_PRIORITY_WEIGHT.get(
                    priority,
                    _PRIORITY_WEIGHT[TaskPriority.NORMAL.value],
                ),
                sequence=next(self._sequence),
                task_id=task_id,
                metadata=metadata or {},
            )

            self._queued_ids.add(task_id)
            await self._queue.put(item)
            return True

    async def get(self) -> QueueItem:
        item = await self._queue.get()

        async with self._lock:
            self._queued_ids.discard(item.task_id)

        return item

    def task_done(self) -> None:
        self._queue.task_done()

    async def join(self) -> None:
        await self._queue.join()

    async def contains(self, task_id: str) -> bool:
        async with self._lock:
            return task_id in self._queued_ids

    def qsize(self) -> int:
        return self._queue.qsize()

    def empty(self) -> bool:
        return self._queue.empty()
