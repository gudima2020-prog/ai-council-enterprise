from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from backend.core.events import Event, EventBus
from backend.core.logging import LoggerManager
from backend.task_engine.queue import QueueItem, TaskQueue


TaskHandler = Callable[[QueueItem], Awaitable[None]]


class TaskWorkerPool:
    """Parallel consumers for TaskQueue with a fixed concurrency limit."""

    def __init__(
        self,
        *,
        queue: TaskQueue,
        event_bus: EventBus,
        handler: TaskHandler,
        worker_count: int,
    ) -> None:
        if worker_count < 1:
            raise ValueError("worker_count must be at least 1.")

        self._queue = queue
        self._event_bus = event_bus
        self._handler = handler
        self._worker_count = worker_count
        self._workers: list[asyncio.Task[None]] = []
        self._active: dict[int, str] = {}
        self._logger = LoggerManager.get_logger("task_worker_pool")
        self._processed = 0
        self._failed = 0

    @property
    def worker_count(self) -> int:
        return self._worker_count

    @property
    def running(self) -> bool:
        return bool(self._workers) and any(
            not worker.done() for worker in self._workers
        )

    async def start(self) -> None:
        if self.running:
            return

        self._workers = [
            asyncio.create_task(
                self._worker_loop(worker_id),
                name=f"task-worker-{worker_id}",
            )
            for worker_id in range(1, self._worker_count + 1)
        ]

        await self._event_bus.publish(
            Event(
                event_type="task.worker_pool.started",
                source="task_worker_pool",
                payload={"worker_count": self._worker_count},
            )
        )

    async def stop(self) -> None:
        workers = list(self._workers)

        for worker in workers:
            worker.cancel()

        if workers:
            await asyncio.gather(*workers, return_exceptions=True)

        self._workers.clear()
        self._active.clear()

        await self._event_bus.publish(
            Event(
                event_type="task.worker_pool.stopped",
                source="task_worker_pool",
                payload={
                    "worker_count": self._worker_count,
                    "processed": self._processed,
                    "failed": self._failed,
                },
            )
        )

    def stats(self) -> dict[str, Any]:
        return {
            "worker_count": self._worker_count,
            "running_workers": sum(
                1 for worker in self._workers if not worker.done()
            ),
            "active_count": len(self._active),
            "active_tasks": {
                str(worker_id): task_id
                for worker_id, task_id in sorted(self._active.items())
            },
            "processed": self._processed,
            "failed": self._failed,
        }

    async def _worker_loop(self, worker_id: int) -> None:
        try:
            while True:
                item = await self._queue.get()
                self._active[worker_id] = item.task_id

                try:
                    await self._event_bus.publish(
                        Event(
                            event_type="task.dequeued",
                            source="task_worker_pool",
                            payload={
                                "task_id": item.task_id,
                                "worker_id": worker_id,
                                "metadata": item.metadata,
                            },
                        )
                    )
                    await self._handler(item)
                    self._processed += 1
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._failed += 1
                    self._logger.exception(
                        "Task worker failed worker_id=%s task_id=%s",
                        worker_id,
                        item.task_id,
                    )
                    await self._event_bus.publish(
                        Event(
                            event_type="task.scheduler.failed",
                            source="task_worker_pool",
                            payload={
                                "task_id": item.task_id,
                                "worker_id": worker_id,
                                "error_type": exc.__class__.__name__,
                                "message": str(exc),
                            },
                        )
                    )
                finally:
                    self._active.pop(worker_id, None)
                    self._queue.task_done()
        except asyncio.CancelledError:
            pass
