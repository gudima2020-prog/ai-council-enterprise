from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from sqlalchemy.orm import Session

from backend.core.events import Event, EventBus
from backend.core.logging import LoggerManager
from backend.database.session import session_scope
from backend.task_engine.cancellation import TaskCancellationToken
from backend.task_engine.enums import TaskStatus, TaskType
from backend.task_engine.queue import QueueItem, TaskQueue
from backend.task_engine.repository import TaskRepository
from backend.task_engine.schemas import TaskLogCreate, TaskRunCreate
from backend.task_engine.state_machine import (
    InvalidTaskTransition,
    TaskStateMachine,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


SessionContextFactory = Callable[[], AbstractContextManager[Session]]


@dataclass(frozen=True)
class TaskExecutionContext:
    task_id: str
    workspace_id: str | None
    task_type: str
    title: str
    description: str
    payload: dict[str, Any]
    executor: str | None
    attempt: int
    timeout_seconds: int
    cancellation: TaskCancellationToken


@dataclass(frozen=True)
class TaskExecutionResult:
    result: dict[str, Any]
    artifacts: list[dict[str, Any]]


@dataclass
class ActiveExecution:
    task: asyncio.Task[None]
    token: TaskCancellationToken
    run_id: str


TaskHandler = Callable[
    [TaskExecutionContext],
    Awaitable[TaskExecutionResult | dict[str, Any]],
]


class UnsupportedTaskHandler(RuntimeError):
    pass


class TaskExecutor:
    INTERRUPTED_STATUSES = {
        TaskStatus.PLANNING.value,
        TaskStatus.RUNNING.value,
        TaskStatus.POST_PROCESSING.value,
    }

    RECOVERABLE_STATUSES = {
        TaskStatus.QUEUED.value,
        TaskStatus.RETRYING.value,
    }

    def __init__(
        self,
        *,
        queue: TaskQueue,
        event_bus: EventBus,
        session_factory: SessionContextFactory = session_scope,
    ) -> None:
        self._queue = queue
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._handlers: dict[str, TaskHandler] = {}
        self._active: dict[str, ActiveExecution] = {}
        self._retry_tasks: set[asyncio.Task[None]] = set()
        self._logger = LoggerManager.get_logger("task_executor")
        self._executed = 0
        self._failed = 0
        self._cancelled = 0
        self._timed_out = 0
        self._recovered = 0
        self.register(TaskType.SYSTEM.value, self._system_handler)

    def register(
        self,
        task_type: str,
        handler: TaskHandler,
        *,
        replace: bool = False,
    ) -> None:
        normalized = task_type.strip().lower()

        if normalized in self._handlers and not replace:
            raise ValueError(
                f"Task handler already registered for type: {normalized}"
            )

        self._handlers[normalized] = handler

    def unregister(self, task_type: str) -> bool:
        return self._handlers.pop(task_type.strip().lower(), None) is not None

    def registered_handlers(self) -> list[str]:
        return sorted(self._handlers)

    def stats(self) -> dict[str, Any]:
        return {
            "registered_handlers": self.registered_handlers(),
            "active": sorted(self._active),
            "active_count": len(self._active),
            "pending_retries": len(self._retry_tasks),
            "executed": self._executed,
            "failed": self._failed,
            "cancelled": self._cancelled,
            "timed_out": self._timed_out,
            "recovered": self._recovered,
        }

    async def execute_queue_item(self, item: QueueItem) -> None:
        claimed = self._claim_task(item.task_id)

        if claimed is None:
            await self._event_bus.publish(
                Event(
                    event_type="task.executor.skipped",
                    source="task_executor",
                    payload={
                        "task_id": item.task_id,
                        "reason": "task_missing_or_not_executable",
                    },
                )
            )
            return

        context, run_id = claimed

        execution_task = asyncio.create_task(
            self._execute_claimed(context, run_id),
            name=f"task-execution-{context.task_id}",
        )
        self._active[context.task_id] = ActiveExecution(
            task=execution_task,
            token=context.cancellation,
            run_id=run_id,
        )

        try:
            await execution_task
        finally:
            self._active.pop(context.task_id, None)

    async def request_cancel(
        self,
        task_id: str,
        reason: str,
    ) -> dict[str, Any] | None:
        active = self._active.get(task_id)

        with self._session_factory() as session:
            repository = TaskRepository(session)
            task = repository.get(task_id)

            if task is None:
                return None

            if task.status == TaskStatus.COMPLETED.value:
                raise InvalidTaskTransition(
                    "Completed Task нельзя отменить."
                )

            if task.status == TaskStatus.CANCELLED.value:
                return {
                    "task_id": task_id,
                    "status": TaskStatus.CANCELLED.value,
                    "cancellation_requested": False,
                    "already_cancelled": True,
                }

            task.cancel_requested_at = utc_now()
            task.cancel_reason = reason
            repository.append_log(
                task.id,
                TaskLogCreate(
                    level="WARNING",
                    message=f"Task cancellation requested: {reason}",
                    metadata={"cancellation_requested": True},
                ),
            )

            if active is None:
                TaskStateMachine.transition(task, TaskStatus.CANCELLED)

        if active is not None:
            active.token.cancel(reason)
            active.task.cancel()

            await self._event_bus.publish(
                Event(
                    event_type="task.cancellation.requested",
                    source="task_executor",
                    payload={
                        "task_id": task_id,
                        "reason": reason,
                    },
                )
            )

            return {
                "task_id": task_id,
                "status": TaskStatus.RUNNING.value,
                "cancellation_requested": True,
                "already_cancelled": False,
            }

        await self._event_bus.publish(
            Event(
                event_type="task.cancelled",
                source="task_executor",
                payload={
                    "task_id": task_id,
                    "reason": reason,
                    "immediate": True,
                },
            )
        )

        return {
            "task_id": task_id,
            "status": TaskStatus.CANCELLED.value,
            "cancellation_requested": True,
            "already_cancelled": False,
        }

    async def retry_task(self, task_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            repository = TaskRepository(session)
            task = repository.get(task_id)

            if task is None:
                return None

            if task.status != TaskStatus.FAILED.value:
                raise InvalidTaskTransition(
                    "Повторный запуск разрешён только для failed Task."
                )

            TaskStateMachine.transition(task, TaskStatus.RETRYING)
            TaskStateMachine.transition(task, TaskStatus.QUEUED)
            priority = task.priority
            workspace_id = task.workspace_id
            retry_count = task.retry_count

            repository.append_log(
                task.id,
                TaskLogCreate(
                    level="INFO",
                    message="Manual retry queued.",
                    metadata={"retry_count": retry_count},
                ),
            )

        added = await self._queue.put(
            task_id=task_id,
            priority=priority,
            metadata={
                "manual_retry": True,
                "workspace_id": workspace_id,
            },
        )

        return {
            "task_id": task_id,
            "status": TaskStatus.QUEUED.value,
            "retry_count": retry_count,
            "enqueued": added,
        }

    async def recover_persistent_queue(self) -> dict[str, int]:
        recovered_items: list[tuple[str, str, str | None]] = []
        interrupted = 0

        with self._session_factory() as session:
            repository = TaskRepository(session)

            active_rows = repository.list_by_statuses(
                sorted(self.INTERRUPTED_STATUSES),
                limit=10_000,
            )

            for row in active_rows:
                previous_status = row.status

                try:
                    TaskStateMachine.transition(row, TaskStatus.FAILED)
                except InvalidTaskTransition:
                    continue

                repository.append_log(
                    row.id,
                    TaskLogCreate(
                        level="ERROR",
                        message=(
                            "Task marked failed during startup recovery "
                            f"because process stopped in status {previous_status}."
                        ),
                        metadata={
                            "recovery": True,
                            "reason": "process_restart",
                            "previous_status": previous_status,
                        },
                    ),
                )
                interrupted += 1

            recoverable_rows = repository.list_by_statuses(
                sorted(self.RECOVERABLE_STATUSES),
                limit=10_000,
            )

            for row in recoverable_rows:
                if row.status == TaskStatus.RETRYING.value:
                    TaskStateMachine.transition(row, TaskStatus.QUEUED)

                recovered_items.append(
                    (row.id, row.priority, row.workspace_id)
                )

        enqueued = 0

        for task_id, priority, workspace_id in recovered_items:
            added = await self._queue.put(
                task_id=task_id,
                priority=priority,
                metadata={
                    "recovered": True,
                    "workspace_id": workspace_id,
                },
            )
            if added:
                enqueued += 1

        self._recovered += enqueued

        result = {
            "enqueued": enqueued,
            "interrupted_failed": interrupted,
        }

        await self._event_bus.publish(
            Event(
                event_type="task.recovery.completed",
                source="task_executor",
                payload=result,
            )
        )

        return result

    async def shutdown(self) -> None:
        active = list(self._active.values())

        for execution in active:
            execution.token.cancel("Application shutdown.")
            execution.task.cancel()

        if active:
            await asyncio.gather(
                *(item.task for item in active),
                return_exceptions=True,
            )

        retry_tasks = list(self._retry_tasks)
        for retry_task in retry_tasks:
            retry_task.cancel()

        if retry_tasks:
            await asyncio.gather(
                *retry_tasks,
                return_exceptions=True,
            )

        self._active.clear()
        self._retry_tasks.clear()

    async def _execute_claimed(
        self,
        context: TaskExecutionContext,
        run_id: str,
    ) -> None:
        handler = self._handlers.get(context.task_type)
        started = perf_counter()

        try:
            if handler is None:
                raise UnsupportedTaskHandler(
                    "No handler registered for task type: "
                    f"{context.task_type}"
                )

            async with asyncio.timeout(context.timeout_seconds):
                raw_result = await handler(context)

            if isinstance(raw_result, TaskExecutionResult):
                execution_result = raw_result
            elif isinstance(raw_result, dict):
                execution_result = TaskExecutionResult(
                    result=raw_result,
                    artifacts=[],
                )
            else:
                raise TypeError(
                    "Task handler must return dict or TaskExecutionResult."
                )

            duration_ms = (perf_counter() - started) * 1000
            self._complete_task(
                task_id=context.task_id,
                run_id=run_id,
                result=execution_result.result,
                duration_ms=duration_ms,
            )
            self._executed += 1

            await self._event_bus.publish(
                Event(
                    event_type="task.executor.completed",
                    source="task_executor",
                    workspace_id=context.workspace_id,
                    payload={
                        "task_id": context.task_id,
                        "run_id": run_id,
                        "duration_ms": duration_ms,
                    },
                )
            )
        except TimeoutError:
            duration_ms = (perf_counter() - started) * 1000
            error = TimeoutError(
                f"Task exceeded timeout of {context.timeout_seconds} seconds."
            )
            retry = self._fail_task(
                task_id=context.task_id,
                run_id=run_id,
                exc=error,
                duration_ms=duration_ms,
                allow_retry=True,
            )
            self._failed += 1
            self._timed_out += 1
            self._spawn_retry(retry)

            await self._event_bus.publish(
                Event(
                    event_type="task.executor.timed_out",
                    source="task_executor",
                    workspace_id=context.workspace_id,
                    payload={
                        "task_id": context.task_id,
                        "run_id": run_id,
                        "timeout_seconds": context.timeout_seconds,
                    },
                )
            )
        except asyncio.CancelledError:
            duration_ms = (perf_counter() - started) * 1000
            self._cancel_task(
                task_id=context.task_id,
                run_id=run_id,
                reason=(
                    context.cancellation.reason
                    or "Task execution cancelled."
                ),
                duration_ms=duration_ms,
            )
            self._cancelled += 1

            await self._event_bus.publish(
                Event(
                    event_type="task.cancelled",
                    source="task_executor",
                    workspace_id=context.workspace_id,
                    payload={
                        "task_id": context.task_id,
                        "run_id": run_id,
                        "reason": context.cancellation.reason,
                    },
                )
            )
        except Exception as exc:
            duration_ms = (perf_counter() - started) * 1000
            retry = self._fail_task(
                task_id=context.task_id,
                run_id=run_id,
                exc=exc,
                duration_ms=duration_ms,
                allow_retry=True,
            )
            self._failed += 1
            self._spawn_retry(retry)

            await self._event_bus.publish(
                Event(
                    event_type="task.executor.failed",
                    source="task_executor",
                    workspace_id=context.workspace_id,
                    payload={
                        "task_id": context.task_id,
                        "run_id": run_id,
                        "duration_ms": duration_ms,
                        "error_type": exc.__class__.__name__,
                        "message": str(exc),
                        "retry_scheduled": retry is not None,
                    },
                )
            )

    def _claim_task(
        self,
        task_id: str,
    ) -> tuple[TaskExecutionContext, str] | None:
        with self._session_factory() as session:
            repository = TaskRepository(session)
            task = repository.get(task_id)

            if task is None:
                return None

            if task.status not in {
                TaskStatus.QUEUED.value,
                TaskStatus.RETRYING.value,
            }:
                return None

            TaskStateMachine.transition(task, TaskStatus.RUNNING)

            attempt = task.retry_count + 1
            run = repository.append_run(
                task.id,
                TaskRunCreate(
                    status=TaskStatus.RUNNING,
                    attempt=attempt,
                    started_at=utc_now(),
                    metadata={"executor": task.executor},
                ),
            )

            repository.append_log(
                task.id,
                TaskLogCreate(
                    run_id=run.id,
                    level="INFO",
                    message="Task execution started.",
                    metadata={
                        "attempt": attempt,
                        "timeout_seconds": task.timeout_seconds,
                    },
                ),
            )

            context = TaskExecutionContext(
                task_id=task.id,
                workspace_id=task.workspace_id,
                task_type=task.task_type,
                title=task.title,
                description=task.description,
                payload=dict(task.payload_json or {}),
                executor=task.executor,
                attempt=attempt,
                timeout_seconds=task.timeout_seconds,
                cancellation=TaskCancellationToken(),
            )

            return context, run.id

    def _complete_task(
        self,
        *,
        task_id: str,
        run_id: str,
        result: dict[str, Any],
        duration_ms: float,
    ) -> None:
        with self._session_factory() as session:
            repository = TaskRepository(session)
            task = repository.get(task_id)
            run = repository.get_run(run_id)

            if task is None or run is None:
                raise RuntimeError("Task or TaskRun disappeared during execution.")

            task.result_json = result
            TaskStateMachine.transition(task, TaskStatus.COMPLETED)

            repository.finish_run(
                run,
                status=TaskStatus.COMPLETED,
                duration_ms=duration_ms,
            )
            repository.append_log(
                task.id,
                TaskLogCreate(
                    run_id=run.id,
                    level="INFO",
                    message="Task execution completed.",
                    metadata={"duration_ms": duration_ms},
                ),
            )

    def _fail_task(
        self,
        *,
        task_id: str,
        run_id: str,
        exc: Exception,
        duration_ms: float,
        allow_retry: bool,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            repository = TaskRepository(session)
            task = repository.get(task_id)
            run = repository.get_run(run_id)

            if task is None or run is None:
                self._logger.error(
                    "Cannot persist task failure task_id=%s run_id=%s",
                    task_id,
                    run_id,
                )
                return None

            try:
                TaskStateMachine.transition(task, TaskStatus.FAILED)
            except InvalidTaskTransition:
                task.status = TaskStatus.FAILED.value
                task.finished_at = utc_now()

            repository.finish_run(
                run,
                status=TaskStatus.FAILED,
                duration_ms=duration_ms,
                error=str(exc),
            )
            repository.append_log(
                task.id,
                TaskLogCreate(
                    run_id=run.id,
                    level="ERROR",
                    message=f"Task execution failed: {exc}",
                    metadata={
                        "duration_ms": duration_ms,
                        "error_type": exc.__class__.__name__,
                    },
                ),
            )

            if not allow_retry or task.retry_count >= task.max_retries:
                return None

            TaskStateMachine.transition(task, TaskStatus.RETRYING)
            delay = min(
                task.retry_delay_seconds * (2 ** (task.retry_count - 1)),
                300.0,
            )

            repository.append_log(
                task.id,
                TaskLogCreate(
                    run_id=run.id,
                    level="WARNING",
                    message=f"Automatic retry scheduled in {delay:.2f}s.",
                    metadata={
                        "retry_count": task.retry_count,
                        "max_retries": task.max_retries,
                        "delay_seconds": delay,
                    },
                ),
            )

            return {
                "task_id": task.id,
                "priority": task.priority,
                "workspace_id": task.workspace_id,
                "delay_seconds": delay,
                "retry_count": task.retry_count,
            }

    def _cancel_task(
        self,
        *,
        task_id: str,
        run_id: str,
        reason: str,
        duration_ms: float,
    ) -> None:
        with self._session_factory() as session:
            repository = TaskRepository(session)
            task = repository.get(task_id)
            run = repository.get_run(run_id)

            if task is None or run is None:
                return

            task.cancel_requested_at = task.cancel_requested_at or utc_now()
            task.cancel_reason = reason

            if task.status != TaskStatus.CANCELLED.value:
                TaskStateMachine.transition(task, TaskStatus.CANCELLED)

            repository.finish_run(
                run,
                status=TaskStatus.CANCELLED,
                duration_ms=duration_ms,
                error=reason,
            )
            repository.append_log(
                task.id,
                TaskLogCreate(
                    run_id=run.id,
                    level="WARNING",
                    message=f"Task execution cancelled: {reason}",
                    metadata={"duration_ms": duration_ms},
                ),
            )

    def _spawn_retry(self, retry: dict[str, Any] | None) -> None:
        if retry is None:
            return

        retry_task = asyncio.create_task(
            self._delayed_retry(retry),
            name=f"task-retry-{retry['task_id']}",
        )
        self._retry_tasks.add(retry_task)
        retry_task.add_done_callback(self._retry_tasks.discard)

    async def _delayed_retry(self, retry: dict[str, Any]) -> None:
        await asyncio.sleep(float(retry["delay_seconds"]))

        with self._session_factory() as session:
            repository = TaskRepository(session)
            task = repository.get(str(retry["task_id"]))

            if task is None or task.status != TaskStatus.RETRYING.value:
                return

            TaskStateMachine.transition(task, TaskStatus.QUEUED)

        added = await self._queue.put(
            task_id=str(retry["task_id"]),
            priority=str(retry["priority"]),
            metadata={
                "automatic_retry": True,
                "workspace_id": retry["workspace_id"],
                "retry_count": retry["retry_count"],
            },
        )

        await self._event_bus.publish(
            Event(
                event_type="task.retry.queued",
                source="task_executor",
                workspace_id=retry["workspace_id"],
                payload={
                    "task_id": retry["task_id"],
                    "retry_count": retry["retry_count"],
                    "enqueued": added,
                },
            )
        )

    @staticmethod
    async def _system_handler(
        context: TaskExecutionContext,
    ) -> dict[str, Any]:
        context.cancellation.raise_if_cancelled()
        action = str(context.payload.get("action", "")).strip().lower()

        if action == "health_check":
            return {
                "status": "ok",
                "task_id": context.task_id,
                "checked_components": context.payload.get(
                    "components",
                    ["task_engine"],
                ),
            }

        if action == "echo":
            return {
                "echo": context.payload.get("value"),
                "task_id": context.task_id,
            }

        if action == "sleep":
            seconds = float(context.payload.get("seconds", 1))

            while seconds > 0:
                context.cancellation.raise_if_cancelled()
                step = min(seconds, 0.1)
                await asyncio.sleep(step)
                seconds -= step

            return {
                "status": "slept",
                "task_id": context.task_id,
            }

        raise UnsupportedTaskHandler(
            "Unsupported system task action. "
            "Allowed actions: health_check, echo, sleep."
        )
