from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core.events import Event, EventBus
from backend.database.session import session_scope
from backend.task_engine.dead_letter_schemas import (
    DeadLetterReplayRequest,
)
from backend.task_engine.enums import TaskPriority, TaskStatus, TaskType
from backend.task_engine.models import (
    TaskDeadLetterModel,
    TaskDeadLetterReplayModel,
    TaskModel,
)
from backend.task_engine.queue import TaskQueue
from backend.task_engine.repository import TaskRepository
from backend.task_engine.schemas import TaskCreate, TaskLogCreate
from backend.task_engine.state_machine import TaskStateMachine
from backend.task_engine.workflow import TaskWorkflowEngine


SessionContextFactory = Callable[[], AbstractContextManager[Session]]


class DeadLetterStatus(StrEnum):
    OPEN = "open"
    REPLAYED = "replayed"
    RESOLVED = "resolved"
    DISCARDED = "discarded"


class DeadLetterReplayStatus(StrEnum):
    CREATED = "created"
    WAITING = "waiting"
    ENQUEUED = "enqueued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class DeadLetterError(ValueError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_time(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def deep_merge(
    base: dict[str, Any],
    patch: dict[str, Any],
) -> dict[str, Any]:
    result = dict(base)

    for key, value in patch.items():
        if (
            isinstance(value, dict)
            and isinstance(result.get(key), dict)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value

    return result


class TaskDeadLetterManager:
    """
    Durable Dead Letter Queue for terminal Task failures.

    A replay always creates a new Task. The original Task and its execution
    evidence remain unchanged, which keeps retries auditable and prevents a
    terminal row from being silently resurrected.
    """

    FINAL_FAILURE_EVENTS = {
        "task.executor.failed",
        "task.executor.timed_out",
    }
    TERMINAL_REPLAY_EVENTS = {
        "task.executor.completed": DeadLetterReplayStatus.COMPLETED,
        "task.executor.failed": DeadLetterReplayStatus.FAILED,
        "task.executor.timed_out": DeadLetterReplayStatus.FAILED,
        "task.cancelled": DeadLetterReplayStatus.CANCELLED,
        "task.skipped": DeadLetterReplayStatus.SKIPPED,
    }

    def __init__(
        self,
        *,
        event_bus: EventBus,
        queue: TaskQueue,
        workflow_engine: TaskWorkflowEngine | None = None,
        session_factory: SessionContextFactory = session_scope,
        max_replays_per_entry: int = 5,
    ) -> None:
        if max_replays_per_entry < 1:
            raise ValueError("max_replays_per_entry must be at least 1.")

        self._event_bus = event_bus
        self._queue = queue
        self._workflow_engine = workflow_engine
        self._session_factory = session_factory
        self._max_replays_per_entry = max_replays_per_entry
        self._lock = asyncio.Lock()
        self._captured = 0
        self._replayed = 0
        self._resolved = 0
        self._discarded = 0

    def stats(self) -> dict[str, Any]:
        with self._session_factory() as session:
            status_rows = session.execute(
                select(
                    TaskDeadLetterModel.status,
                    func.count(TaskDeadLetterModel.id),
                ).group_by(TaskDeadLetterModel.status)
            ).all()
            replay_rows = session.execute(
                select(
                    TaskDeadLetterReplayModel.status,
                    func.count(TaskDeadLetterReplayModel.id),
                ).group_by(TaskDeadLetterReplayModel.status)
            ).all()

        return {
            "entries": {status: count for status, count in status_rows},
            "replays": {status: count for status, count in replay_rows},
            "max_replays_per_entry": self._max_replays_per_entry,
            "runtime": {
                "captured": self._captured,
                "replayed": self._replayed,
                "resolved": self._resolved,
                "discarded": self._discarded,
            },
        }

    def list_entries(
        self,
        *,
        status: DeadLetterStatus | None = None,
        task_id: str | None = None,
        workspace_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(TaskDeadLetterModel)

            if status is not None:
                statement = statement.where(
                    TaskDeadLetterModel.status == status.value
                )
            if task_id is not None:
                statement = statement.where(
                    TaskDeadLetterModel.task_id == task_id
                )
            if workspace_id is not None:
                statement = statement.where(
                    TaskDeadLetterModel.workspace_id == workspace_id
                )

            statement = (
                statement
                .order_by(TaskDeadLetterModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            rows = list(session.scalars(statement).all())
            return [self._serialize_entry(row) for row in rows]

    def get_entry(self, entry_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            entry = session.get(TaskDeadLetterModel, entry_id)

            if entry is None:
                return None

            replay_statement = (
                select(TaskDeadLetterReplayModel)
                .where(
                    TaskDeadLetterReplayModel.dead_letter_id == entry_id
                )
                .order_by(TaskDeadLetterReplayModel.created_at.asc())
            )
            replays = list(session.scalars(replay_statement).all())
            payload = self._serialize_entry(entry)
            payload["replays"] = [
                self._serialize_replay(replay) for replay in replays
            ]
            return payload

    async def capture_from_event(
        self,
        event: Event,
    ) -> dict[str, Any] | None:
        if event.event_type not in self.FINAL_FAILURE_EVENTS:
            return None

        task_id = str(event.payload.get("task_id", "")).strip()
        if not task_id:
            return None

        reason_code = (
            "execution_timeout"
            if event.event_type == "task.executor.timed_out"
            else "execution_failed"
        )

        return await self.capture_task(
            task_id=task_id,
            reason_code=reason_code,
            error_type=event.payload.get("error_type"),
            error_message=event.payload.get("message"),
            source_event_type=event.event_type,
            source_payload=event.payload,
        )

    async def capture_task(
        self,
        *,
        task_id: str,
        reason_code: str,
        error_type: str | None = None,
        error_message: str | None = None,
        source_event_type: str | None = None,
        source_payload: dict[str, Any] | None = None,
        actor_id: str | None = None,
    ) -> dict[str, Any] | None:
        created: dict[str, Any] | None = None

        async with self._lock:
            with self._session_factory() as session:
                repository = TaskRepository(session)
                task = repository.get_full(task_id)

                if task is None:
                    return None
                if task.status != TaskStatus.FAILED.value:
                    return {
                        "captured": False,
                        "task_id": task.id,
                        "status": task.status,
                        "reason": "task_not_finally_failed",
                    }

                existing_statement = (
                    select(TaskDeadLetterModel)
                    .where(
                        TaskDeadLetterModel.task_id == task.id,
                        TaskDeadLetterModel.status.in_(
                            [
                                DeadLetterStatus.OPEN.value,
                                DeadLetterStatus.REPLAYED.value,
                                DeadLetterStatus.RESOLVED.value,
                                DeadLetterStatus.DISCARDED.value,
                            ]
                        ),
                    )
                    .order_by(TaskDeadLetterModel.created_at.desc())
                    .limit(1)
                )
                existing = session.scalar(existing_statement)

                if existing is not None:
                    payload = self._serialize_entry(existing)
                    payload.update(
                        {
                            "captured": False,
                            "duplicate": True,
                        }
                    )
                    return payload

                latest_run = task.runs[-1] if task.runs else None
                effective_error = (
                    error_message
                    or (latest_run.error if latest_run is not None else None)
                    or "Task reached failed status after retry exhaustion."
                )
                effective_error_type = (
                    error_type
                    or self._latest_error_type(task)
                )

                row = TaskDeadLetterModel(
                    task_id=task.id,
                    workspace_id=task.workspace_id,
                    status=DeadLetterStatus.OPEN.value,
                    reason_code=reason_code,
                    error_type=effective_error_type,
                    error_message=effective_error,
                    source_event_type=source_event_type,
                    task_snapshot_json=self._task_snapshot(task),
                    failure_snapshot_json={
                        "source_event_type": source_event_type,
                        "source_payload": source_payload or {},
                        "actor_id": actor_id,
                        "latest_run": self._run_snapshot(latest_run),
                        "recent_logs": [
                            self._log_snapshot(item)
                            for item in task.logs[-20:]
                        ],
                    },
                )
                session.add(row)
                session.flush()
                created = self._serialize_entry(row)
                created["captured"] = True
                self._captured += 1

        assert created is not None
        await self._event_bus.publish(
            Event(
                event_type="task.dead_letter.created",
                source="task_dead_letter_manager",
                workspace_id=created["workspace_id"],
                payload={
                    "dead_letter_id": created["id"],
                    "task_id": created["task_id"],
                    "reason_code": created["reason_code"],
                    "error_type": created["error_type"],
                },
            )
        )
        return created

    async def replay(
        self,
        entry_id: str,
        request: DeadLetterReplayRequest,
    ) -> dict[str, Any] | None:
        replay_id: str
        new_task_id: str
        workspace_id: str | None
        source_task_id: str
        task_created_payload: dict[str, Any]

        async with self._lock:
            with self._session_factory() as session:
                entry = session.get(TaskDeadLetterModel, entry_id)

                if entry is None:
                    return None
                if entry.status == DeadLetterStatus.DISCARDED.value:
                    raise DeadLetterError(
                        "Discarded dead-letter entry нельзя воспроизвести."
                    )
                if (
                    entry.status == DeadLetterStatus.RESOLVED.value
                    and not request.force
                ):
                    raise DeadLetterError(
                        "Resolved dead-letter entry уже успешно восстановлена. "
                        "Для повторного replay требуется force."
                    )
                if (
                    entry.replay_count >= self._max_replays_per_entry
                    and not request.force
                ):
                    raise DeadLetterError(
                        "Достигнут лимит replay для dead-letter entry. "
                        "Используйте force только после ручной проверки."
                    )

                if request.idempotency_key is not None:
                    existing_statement = select(
                        TaskDeadLetterReplayModel
                    ).where(
                        TaskDeadLetterReplayModel.dead_letter_id
                        == entry.id,
                        TaskDeadLetterReplayModel.idempotency_key
                        == request.idempotency_key,
                    )
                    existing = session.scalar(existing_statement)

                    if existing is not None:
                        payload = self._serialize_replay(existing)
                        payload.update(
                            {
                                "dead_letter_id": entry.id,
                                "idempotent": True,
                            }
                        )
                        return payload

                snapshot = dict(entry.task_snapshot_json or {})
                original_payload = snapshot.get("payload", {})

                if not isinstance(original_payload, dict):
                    original_payload = {}

                payload = deep_merge(
                    original_payload,
                    request.payload_patch,
                )
                payload["_replay"] = {
                    "dead_letter_id": entry.id,
                    "source_task_id": entry.task_id,
                    "replay_number": entry.replay_count + 1,
                    "actor_id": request.actor_id,
                    "reason": request.reason,
                }

                repository = TaskRepository(session)
                new_task = repository.create(
                    TaskCreate(
                        workspace_id=snapshot.get("workspace_id"),
                        task_type=TaskType(
                            snapshot.get(
                                "task_type",
                                TaskType.SYSTEM.value,
                            )
                        ),
                        priority=(
                            request.priority
                            or TaskPriority(
                                snapshot.get(
                                    "priority",
                                    TaskPriority.NORMAL.value,
                                )
                            )
                        ),
                        title=str(snapshot.get("title") or "DLQ replay"),
                        description=str(snapshot.get("description") or ""),
                        payload=payload,
                        creator=f"dlq-replay:{request.actor_id}",
                        executor=snapshot.get("executor"),
                        max_retries=(
                            request.max_retries
                            if request.max_retries is not None
                            else int(snapshot.get("max_retries", 0))
                        ),
                        retry_delay_seconds=(
                            request.retry_delay_seconds
                            if request.retry_delay_seconds is not None
                            else float(
                                snapshot.get("retry_delay_seconds", 1.0)
                            )
                        ),
                        timeout_seconds=(
                            request.timeout_seconds
                            if request.timeout_seconds is not None
                            else int(snapshot.get("timeout_seconds", 300))
                        ),
                    )
                )
                repository.append_log(
                    new_task.id,
                    TaskLogCreate(
                        level="INFO",
                        message="Task created from Dead Letter Queue replay.",
                        metadata={
                            "dead_letter_id": entry.id,
                            "source_task_id": entry.task_id,
                            "actor_id": request.actor_id,
                            "reason": request.reason,
                        },
                    ),
                )

                replay = TaskDeadLetterReplayModel(
                    dead_letter_id=entry.id,
                    source_task_id=entry.task_id,
                    replay_task_id=new_task.id,
                    idempotency_key=request.idempotency_key,
                    status=DeadLetterReplayStatus.CREATED.value,
                    actor_id=request.actor_id,
                    reason=request.reason,
                    request_json=request.model_dump(mode="json"),
                )
                session.add(replay)
                session.flush()

                replay_id = replay.id
                new_task_id = new_task.id
                workspace_id = new_task.workspace_id
                source_task_id = entry.task_id
                task_created_payload = {
                    "task_id": new_task.id,
                    "task_type": new_task.task_type,
                    "priority": new_task.priority,
                    "status": new_task.status,
                    "dead_letter_id": entry.id,
                    "replay_id": replay.id,
                }

        await self._event_bus.publish(
            Event(
                event_type="task.created",
                source="task_dead_letter_manager",
                workspace_id=workspace_id,
                payload=task_created_payload,
            )
        )

        try:
            if self._workflow_engine is not None:
                enqueue_result = await self._workflow_engine.enqueue_task(
                    new_task_id,
                    source="dead_letter_replay",
                )
            else:
                with self._session_factory() as session:
                    task = TaskRepository(session).get(new_task_id)
                    if task is None:
                        raise DeadLetterError(
                            "Replay Task исчезла до постановки в очередь."
                        )
                    TaskStateMachine.transition(task, TaskStatus.QUEUED)
                    priority = task.priority
                    workspace_id = task.workspace_id

                added = await self._queue.put(
                    task_id=new_task_id,
                    priority=priority,
                    metadata={
                        "workspace_id": workspace_id,
                        "source": "dead_letter_replay",
                    },
                )
                enqueue_result = {
                    "task_id": new_task_id,
                    "status": TaskStatus.QUEUED.value,
                    "enqueued": added,
                }
        except Exception as exc:
            with self._session_factory() as session:
                replay = session.get(TaskDeadLetterReplayModel, replay_id)
                if replay is not None:
                    replay.status = DeadLetterReplayStatus.FAILED.value
                    replay.error = str(exc)
                    replay.finished_at = utc_now()
            raise

        enqueue_result = enqueue_result or {
            "task_id": new_task_id,
            "status": TaskStatus.CREATED.value,
            "enqueued": False,
        }
        replay_status = self._status_from_enqueue(enqueue_result)

        with self._session_factory() as session:
            entry = session.get(TaskDeadLetterModel, entry_id)
            replay = session.get(TaskDeadLetterReplayModel, replay_id)

            if entry is None or replay is None:
                raise DeadLetterError(
                    "Dead-letter replay state disappeared during enqueue."
                )

            replay.status = replay_status.value
            entry.status = DeadLetterStatus.REPLAYED.value
            entry.replay_count += 1
            entry.last_replayed_task_id = new_task_id
            result = self._serialize_replay(replay)
            result.update(
                {
                    "dead_letter_id": entry.id,
                    "dead_letter_status": entry.status,
                    "replay_count": entry.replay_count,
                    "enqueue": enqueue_result,
                    "idempotent": False,
                }
            )
            self._replayed += 1

        await self._event_bus.publish(
            Event(
                event_type="task.dead_letter.replayed",
                source="task_dead_letter_manager",
                workspace_id=workspace_id,
                payload={
                    "dead_letter_id": entry_id,
                    "replay_id": replay_id,
                    "source_task_id": source_task_id,
                    "task_id": new_task_id,
                    "actor_id": request.actor_id,
                    "status": replay_status.value,
                },
            )
        )
        return result

    async def discard(
        self,
        *,
        entry_id: str,
        actor_id: str,
        reason: str,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            entry = session.get(TaskDeadLetterModel, entry_id)

            if entry is None:
                return None
            if entry.status == DeadLetterStatus.RESOLVED.value:
                raise DeadLetterError(
                    "Resolved dead-letter entry нельзя discard."
                )

            entry.status = DeadLetterStatus.DISCARDED.value
            entry.resolved_by = actor_id
            entry.resolution_note = reason
            entry.resolved_at = utc_now()
            payload = self._serialize_entry(entry)
            self._discarded += 1

        await self._event_bus.publish(
            Event(
                event_type="task.dead_letter.discarded",
                source="task_dead_letter_manager",
                workspace_id=payload["workspace_id"],
                payload={
                    "dead_letter_id": entry_id,
                    "task_id": payload["task_id"],
                    "actor_id": actor_id,
                    "reason": reason,
                },
            )
        )
        return payload

    async def handle_replay_terminal_event(
        self,
        event: Event,
    ) -> dict[str, Any] | None:
        target_status = self.TERMINAL_REPLAY_EVENTS.get(event.event_type)

        if target_status is None:
            return None

        task_id = str(event.payload.get("task_id", "")).strip()
        if not task_id:
            return None

        with self._session_factory() as session:
            statement = (
                select(TaskDeadLetterReplayModel)
                .where(
                    TaskDeadLetterReplayModel.replay_task_id == task_id
                )
                .order_by(TaskDeadLetterReplayModel.created_at.desc())
                .limit(1)
            )
            replay = session.scalar(statement)

            if replay is None:
                return None

            task = session.get(TaskModel, task_id)

            if (
                target_status == DeadLetterReplayStatus.FAILED
                and task is not None
                and task.status != TaskStatus.FAILED.value
            ):
                return None

            replay.status = target_status.value
            replay.finished_at = utc_now()
            replay.error = event.payload.get("message")
            entry = session.get(
                TaskDeadLetterModel,
                replay.dead_letter_id,
            )

            if (
                entry is not None
                and target_status == DeadLetterReplayStatus.COMPLETED
            ):
                entry.status = DeadLetterStatus.RESOLVED.value
                entry.resolved_at = utc_now()
                entry.resolved_by = "task_executor"
                entry.resolution_note = (
                    f"Replay Task {task_id} completed successfully."
                )
                self._resolved += 1

            payload = self._serialize_replay(replay)
            payload["dead_letter_status"] = (
                entry.status if entry is not None else None
            )

        await self._event_bus.publish(
            Event(
                event_type=(
                    "task.dead_letter.replay."
                    f"{target_status.value}"
                ),
                source="task_dead_letter_manager",
                payload={
                    "dead_letter_id": payload["dead_letter_id"],
                    "replay_id": payload["id"],
                    "task_id": task_id,
                    "status": target_status.value,
                },
            )
        )
        return payload

    async def reconcile(self) -> dict[str, int]:
        with self._session_factory() as session:
            failed_task_ids = list(
                session.scalars(
                    select(TaskModel.id)
                    .where(TaskModel.status == TaskStatus.FAILED.value)
                    .order_by(TaskModel.created_at.asc())
                ).all()
            )

        captured = 0
        duplicates = 0

        for task_id in failed_task_ids:
            result = await self.capture_task(
                task_id=task_id,
                reason_code="startup_reconcile",
                source_event_type="task.dead_letter.reconcile",
                source_payload={"startup_reconcile": True},
            )

            if result is None:
                continue
            if result.get("captured"):
                captured += 1
            elif result.get("duplicate"):
                duplicates += 1

        return {
            "failed_tasks": len(failed_task_ids),
            "captured": captured,
            "duplicates": duplicates,
        }

    def verify_integrity(self) -> dict[str, Any]:
        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        with self._session_factory() as session:
            duplicate_rows = session.execute(
                select(
                    TaskDeadLetterModel.task_id,
                    func.count(TaskDeadLetterModel.id),
                )
                .where(
                    TaskDeadLetterModel.status
                    != DeadLetterStatus.DISCARDED.value
                )
                .group_by(TaskDeadLetterModel.task_id)
                .having(func.count(TaskDeadLetterModel.id) > 1)
            ).all()

            for task_id, count in duplicate_rows:
                errors.append(
                    {
                        "type": "duplicate_active_entries",
                        "task_id": task_id,
                        "count": count,
                    }
                )

            resolved_entries = list(
                session.scalars(
                    select(TaskDeadLetterModel).where(
                        TaskDeadLetterModel.status
                        == DeadLetterStatus.RESOLVED.value
                    )
                ).all()
            )

            for entry in resolved_entries:
                completed_count = session.scalar(
                    select(func.count(TaskDeadLetterReplayModel.id)).where(
                        TaskDeadLetterReplayModel.dead_letter_id == entry.id,
                        TaskDeadLetterReplayModel.status
                        == DeadLetterReplayStatus.COMPLETED.value,
                    )
                ) or 0
                if completed_count == 0:
                    errors.append(
                        {
                            "type": "resolved_without_completed_replay",
                            "dead_letter_id": entry.id,
                        }
                    )

            replay_rows = list(
                session.scalars(
                    select(TaskDeadLetterReplayModel)
                ).all()
            )
            for replay in replay_rows:
                if session.get(TaskModel, replay.replay_task_id) is None:
                    warnings.append(
                        {
                            "type": "replay_task_missing",
                            "dead_letter_id": replay.dead_letter_id,
                            "replay_id": replay.id,
                            "replay_task_id": replay.replay_task_id,
                        }
                    )

        return {
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
        }

    @staticmethod
    def _status_from_enqueue(
        result: dict[str, Any],
    ) -> DeadLetterReplayStatus:
        if result.get("enqueued"):
            return DeadLetterReplayStatus.ENQUEUED

        status = result.get("status")
        if status == TaskStatus.WAITING.value:
            return DeadLetterReplayStatus.WAITING
        if status == TaskStatus.RUNNING.value:
            return DeadLetterReplayStatus.RUNNING
        if status == TaskStatus.COMPLETED.value:
            return DeadLetterReplayStatus.COMPLETED
        if status == TaskStatus.CANCELLED.value:
            return DeadLetterReplayStatus.CANCELLED
        if status == TaskStatus.SKIPPED.value:
            return DeadLetterReplayStatus.SKIPPED
        if status == TaskStatus.FAILED.value:
            return DeadLetterReplayStatus.FAILED
        return DeadLetterReplayStatus.CREATED

    @staticmethod
    def _latest_error_type(task: TaskModel) -> str | None:
        for log in reversed(task.logs):
            value = (log.metadata_json or {}).get("error_type")
            if value:
                return str(value)
        return None

    @staticmethod
    def _task_snapshot(task: TaskModel) -> dict[str, Any]:
        return {
            "id": task.id,
            "workspace_id": task.workspace_id,
            "task_type": task.task_type,
            "priority": task.priority,
            "status": task.status,
            "title": task.title,
            "description": task.description,
            "payload": task.payload_json,
            "result": task.result_json,
            "creator": task.creator,
            "executor": task.executor,
            "retry_count": task.retry_count,
            "max_retries": task.max_retries,
            "retry_delay_seconds": task.retry_delay_seconds,
            "timeout_seconds": task.timeout_seconds,
            "created_at": _serialize_time(task.created_at),
            "started_at": _serialize_time(task.started_at),
            "finished_at": _serialize_time(task.finished_at),
        }

    @staticmethod
    def _run_snapshot(run) -> dict[str, Any] | None:
        if run is None:
            return None
        return {
            "id": run.id,
            "status": run.status,
            "attempt": run.attempt,
            "started_at": _serialize_time(run.started_at),
            "finished_at": _serialize_time(run.finished_at),
            "duration_ms": run.duration_ms,
            "error": run.error,
            "metadata": run.metadata_json,
        }

    @staticmethod
    def _log_snapshot(log) -> dict[str, Any]:
        return {
            "id": log.id,
            "run_id": log.run_id,
            "level": log.level,
            "message": log.message,
            "metadata": log.metadata_json,
            "created_at": _serialize_time(log.created_at),
        }

    @staticmethod
    def _serialize_entry(row: TaskDeadLetterModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "task_id": row.task_id,
            "workspace_id": row.workspace_id,
            "status": row.status,
            "reason_code": row.reason_code,
            "error_type": row.error_type,
            "error_message": row.error_message,
            "source_event_type": row.source_event_type,
            "task_snapshot": row.task_snapshot_json,
            "failure_snapshot": row.failure_snapshot_json,
            "replay_count": row.replay_count,
            "last_replayed_task_id": row.last_replayed_task_id,
            "resolved_by": row.resolved_by,
            "resolution_note": row.resolution_note,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "resolved_at": row.resolved_at,
        }

    @staticmethod
    def _serialize_replay(
        row: TaskDeadLetterReplayModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "dead_letter_id": row.dead_letter_id,
            "source_task_id": row.source_task_id,
            "replay_task_id": row.replay_task_id,
            "idempotency_key": row.idempotency_key,
            "status": row.status,
            "actor_id": row.actor_id,
            "reason": row.reason,
            "request": row.request_json,
            "error": row.error,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "finished_at": row.finished_at,
        }
