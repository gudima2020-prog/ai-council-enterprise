from __future__ import annotations

from typing import Any

from backend.core.events import Event, EventBus
from backend.task_engine.enums import TaskStatus
from backend.task_engine.repository import TaskRepository
from backend.task_engine.schemas import (
    TaskArtifactCreate,
    TaskCreate,
    TaskLogCreate,
    TaskRunCreate,
    TaskTransitionRequest,
    TaskUpdate,
)
from backend.task_engine.state_machine import (
    InvalidTaskTransition,
    TaskStateMachine,
)


class TaskService:
    def __init__(
        self,
        repository: TaskRepository,
        event_bus: EventBus,
    ) -> None:
        self._repository = repository
        self._event_bus = event_bus

    async def create_task(self, data: TaskCreate) -> dict[str, Any]:
        row = self._repository.create(data)

        await self._event_bus.publish(
            Event(
                event_type="task.created",
                source="task_service",
                workspace_id=row.workspace_id,
                payload={
                    "task_id": row.id,
                    "task_type": row.task_type,
                    "priority": row.priority,
                    "status": row.status,
                },
            )
        )

        return self._serialize(row)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = self._repository.get_full(task_id)
        if row is None:
            return None
        return self._serialize_full(row)

    def list_tasks(
        self,
        *,
        workspace_id: str | None = None,
        status: str | None = None,
        task_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        rows = self._repository.list(
            workspace_id=workspace_id,
            status=status,
            task_type=task_type,
            limit=limit,
            offset=offset,
        )
        return [self._serialize(row) for row in rows]

    async def update_task(
        self,
        task_id: str,
        data: TaskUpdate,
    ) -> dict[str, Any] | None:
        row = self._repository.get(task_id)
        if row is None:
            return None

        row = self._repository.update(row, data)

        await self._event_bus.publish(
            Event(
                event_type="task.updated",
                source="task_service",
                workspace_id=row.workspace_id,
                payload={
                    "task_id": row.id,
                    "status": row.status,
                },
            )
        )

        return self._serialize(row)

    async def transition_task(
        self,
        task_id: str,
        request: TaskTransitionRequest,
    ) -> dict[str, Any] | None:
        row = self._repository.get(task_id)
        if row is None:
            return None

        result = TaskStateMachine.transition(
            row,
            request.status,
        )
        self._repository.session.flush()

        log_message = (
            "Task status changed: "
            f"{result.previous_status.value} -> {result.current_status.value}"
        )

        if request.reason:
            log_message += f". Reason: {request.reason}"

        self._repository.append_log(
            task_id,
            TaskLogCreate(
                level="INFO",
                message=log_message,
                metadata={
                    "previous_status": result.previous_status.value,
                    "current_status": result.current_status.value,
                    **request.metadata,
                },
            ),
        )

        await self._event_bus.publish(
            Event(
                event_type=f"task.{result.current_status.value}",
                source="task_state_machine",
                workspace_id=row.workspace_id,
                payload={
                    "task_id": row.id,
                    "previous_status": result.previous_status.value,
                    "status": result.current_status.value,
                    "reason": request.reason,
                    "retry_count": row.retry_count,
                    "max_retries": row.max_retries,
                    "metadata": request.metadata,
                },
            )
        )

        return self._serialize(row)

    def get_allowed_transitions(
        self,
        task_id: str,
    ) -> dict[str, Any] | None:
        row = self._repository.get(task_id)
        if row is None:
            return None

        return {
            "task_id": row.id,
            "status": row.status,
            "terminal": TaskStatus(row.status)
            in TaskStateMachine.TERMINAL_STATUSES,
            "allowed_transitions": [
                item.value
                for item in TaskStateMachine.allowed_transitions(row.status)
            ],
        }

    async def delete_task(self, task_id: str) -> bool:
        row = self._repository.get(task_id)
        if row is None:
            return False

        workspace_id = row.workspace_id
        self._repository.delete(row)

        await self._event_bus.publish(
            Event(
                event_type="task.deleted",
                source="task_service",
                workspace_id=workspace_id,
                payload={"task_id": task_id},
            )
        )

        return True

    def append_run(
        self,
        task_id: str,
        data: TaskRunCreate,
    ) -> dict[str, Any] | None:
        if self._repository.get(task_id) is None:
            return None

        row = self._repository.append_run(task_id, data)
        return {
            "id": row.id,
            "task_id": row.task_id,
            "status": row.status,
            "attempt": row.attempt,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "duration_ms": row.duration_ms,
            "error": row.error,
            "metadata": row.metadata_json,
            "created_at": row.created_at,
        }

    def append_log(
        self,
        task_id: str,
        data: TaskLogCreate,
    ) -> dict[str, Any] | None:
        if self._repository.get(task_id) is None:
            return None

        row = self._repository.append_log(task_id, data)
        return {
            "id": row.id,
            "task_id": row.task_id,
            "run_id": row.run_id,
            "level": row.level,
            "message": row.message,
            "metadata": row.metadata_json,
            "created_at": row.created_at,
        }

    def append_artifact(
        self,
        task_id: str,
        data: TaskArtifactCreate,
    ) -> dict[str, Any] | None:
        if self._repository.get(task_id) is None:
            return None

        row = self._repository.append_artifact(task_id, data)
        return {
            "id": row.id,
            "task_id": row.task_id,
            "run_id": row.run_id,
            "artifact_type": row.artifact_type,
            "name": row.name,
            "path": row.path,
            "mime_type": row.mime_type,
            "size_bytes": row.size_bytes,
            "checksum": row.checksum,
            "metadata": row.metadata_json,
            "created_at": row.created_at,
        }

    @staticmethod
    def _serialize(row) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "task_type": row.task_type,
            "priority": row.priority,
            "status": row.status,
            "title": row.title,
            "description": row.description,
            "payload": row.payload_json,
            "result": row.result_json,
            "creator": row.creator,
            "executor": row.executor,
            "retry_count": row.retry_count,
            "max_retries": row.max_retries,
            "retry_delay_seconds": row.retry_delay_seconds,
            "timeout_seconds": row.timeout_seconds,
            "cancel_requested_at": row.cancel_requested_at,
            "cancel_reason": row.cancel_reason,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
        }

    @classmethod
    def _serialize_full(cls, row) -> dict[str, Any]:
        payload = cls._serialize(row)
        payload["runs"] = [
            {
                "id": item.id,
                "status": item.status,
                "attempt": item.attempt,
                "started_at": item.started_at,
                "finished_at": item.finished_at,
                "duration_ms": item.duration_ms,
                "error": item.error,
                "metadata": item.metadata_json,
                "created_at": item.created_at,
            }
            for item in row.runs
        ]
        payload["logs"] = [
            {
                "id": item.id,
                "run_id": item.run_id,
                "level": item.level,
                "message": item.message,
                "metadata": item.metadata_json,
                "created_at": item.created_at,
            }
            for item in row.logs
        ]
        payload["artifacts"] = [
            {
                "id": item.id,
                "run_id": item.run_id,
                "artifact_type": item.artifact_type,
                "name": item.name,
                "path": item.path,
                "mime_type": item.mime_type,
                "size_bytes": item.size_bytes,
                "checksum": item.checksum,
                "metadata": item.metadata_json,
                "created_at": item.created_at,
            }
            for item in row.artifacts
        ]
        return payload


__all__ = [
    "InvalidTaskTransition",
    "TaskService",
]
