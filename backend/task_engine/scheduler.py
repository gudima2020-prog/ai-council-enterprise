from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from backend.core.events import Event, EventBus
from backend.task_engine.queue import QueueItem, TaskQueue
from backend.task_engine.worker_pool import TaskWorkerPool


TaskHandler = Callable[[QueueItem], Awaitable[None]]


class TaskScheduler:
    """Priority scheduler backed by a parallel worker pool."""

    def __init__(
        self,
        *,
        queue: TaskQueue,
        event_bus: EventBus,
        handler: TaskHandler | None = None,
        worker_count: int = 1,
    ) -> None:
        self._queue = queue
        self._event_bus = event_bus
        self._handler = handler or self._noop_handler
        self._pool = TaskWorkerPool(
            queue=queue,
            event_bus=event_bus,
            handler=self._handler,
            worker_count=worker_count,
        )

    @property
    def running(self) -> bool:
        return self._pool.running

    @property
    def worker_count(self) -> int:
        return self._pool.worker_count

    async def start(self) -> None:
        if self.running:
            return

        await self._pool.start()

        await self._event_bus.publish(
            Event(
                event_type="task.scheduler.started",
                source="task_scheduler",
                payload={"worker_count": self.worker_count},
            )
        )

    async def stop(self) -> None:
        if not self.running:
            return

        await self._pool.stop()

        stats = self._pool.stats()
        await self._event_bus.publish(
            Event(
                event_type="task.scheduler.stopped",
                source="task_scheduler",
                payload={
                    "processed": stats["processed"],
                    "failed": stats["failed"],
                    "worker_count": stats["worker_count"],
                },
            )
        )

    async def enqueue(
        self,
        *,
        task_id: str,
        priority: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        added = await self._queue.put(
            task_id=task_id,
            priority=priority,
            metadata=metadata,
        )

        if added:
            await self._event_bus.publish(
                Event(
                    event_type="task.enqueued",
                    source="task_scheduler",
                    payload={
                        "task_id": task_id,
                        "priority": priority,
                        "queue_size": self._queue.qsize(),
                    },
                )
            )

        return added

    def stats(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "queue_size": self._queue.qsize(),
            **self._pool.stats(),
        }

    @staticmethod
    async def _noop_handler(item: QueueItem) -> None:
        return None
