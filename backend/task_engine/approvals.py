from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.events import Event, EventBus
from backend.database.session import session_scope
from backend.task_engine.approval_schemas import (
    ApprovalDecision,
    ApprovalDecisionRequest,
    ApprovalGateDefinition,
    ApprovalStatus,
    TaskApprovalRequest,
)
from backend.task_engine.enums import TaskStatus
from backend.task_engine.models import TaskApprovalModel, TaskModel
from backend.task_engine.repository import TaskRepository
from backend.task_engine.schemas import TaskLogCreate
from backend.task_engine.state_machine import (
    InvalidTaskTransition,
    TaskStateMachine,
)

if TYPE_CHECKING:
    from backend.task_engine.workflow import TaskWorkflowEngine


SessionContextFactory = Callable[[], AbstractContextManager[Session]]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class ApprovalError(ValueError):
    pass


class ApprovalGateRuntime:
    """Session-bound approval persistence used by workflow preparation."""

    TERMINAL_TASK_STATUSES = {
        TaskStatus.COMPLETED.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
        TaskStatus.SKIPPED.value,
    }

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(
        self,
        approval_id: str,
    ) -> TaskApprovalModel | None:
        return self.session.get(TaskApprovalModel, approval_id)

    def get_for_task(
        self,
        task_id: str,
        gate_key: str | None = None,
    ) -> TaskApprovalModel | None:
        statement = select(TaskApprovalModel).where(
            TaskApprovalModel.task_id == task_id
        )

        if gate_key is not None:
            statement = statement.where(
                TaskApprovalModel.gate_key == gate_key
            )

        statement = statement.order_by(
            TaskApprovalModel.created_at.desc()
        )
        return self.session.scalar(statement)

    def ensure_for_task(
        self,
        task: TaskModel,
        spec: ApprovalGateDefinition | None = None,
        *,
        requested_by: str | None = None,
    ) -> dict[str, Any] | None:
        if spec is not None and not spec.required:
            return None

        row = self.get_for_task(
            task.id,
            spec.gate_key if spec is not None else None,
        )

        if row is None and spec is not None:
            row = self._create(
                task=task,
                gate_key=spec.gate_key,
                prompt=spec.prompt,
                requested_by=requested_by,
                expires_in_seconds=spec.expires_in_seconds,
                metadata=spec.metadata,
            )

        if row is None:
            return None

        self._expire_if_needed(row, task)

        if row.status == ApprovalStatus.PENDING.value:
            self._move_task_to_waiting(task)

        return self.serialize(row)

    def request(
        self,
        task: TaskModel,
        request: TaskApprovalRequest,
    ) -> dict[str, Any]:
        if task.status not in {
            TaskStatus.CREATED.value,
            TaskStatus.WAITING.value,
        }:
            raise ApprovalError(
                "Approval можно запросить только для Task "
                "в статусе created или waiting."
            )

        existing = self.get_for_task(task.id, request.gate_key)

        if existing is not None:
            self._expire_if_needed(existing, task)

            if existing.status == ApprovalStatus.PENDING.value:
                return self.serialize(existing)

            raise ApprovalError(
                "Approval Gate с таким gate_key уже завершён. "
                "Создайте новую Task или используйте другой gate_key."
            )

        row = self._create(
            task=task,
            gate_key=request.gate_key,
            prompt=request.prompt,
            requested_by=request.requested_by,
            expires_in_seconds=request.expires_in_seconds,
            metadata=request.metadata,
        )
        self._move_task_to_waiting(task)
        return self.serialize(row)

    def list(
        self,
        *,
        status: ApprovalStatus | None = None,
        workspace_id: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        statement = select(TaskApprovalModel)

        if status is not None:
            statement = statement.where(
                TaskApprovalModel.status == status.value
            )
        if workspace_id is not None:
            statement = statement.where(
                TaskApprovalModel.workspace_id == workspace_id
            )
        if task_id is not None:
            statement = statement.where(
                TaskApprovalModel.task_id == task_id
            )

        statement = (
            statement
            .order_by(TaskApprovalModel.created_at.desc())
            .offset(offset)
            .limit(limit)
        )

        rows = list(self.session.scalars(statement).all())
        now = utc_now()

        for row in rows:
            task = self.session.get(TaskModel, row.task_id)
            if task is not None:
                self._expire_if_needed(row, task, now=now)

        return [self.serialize(row) for row in rows]

    def _create(
        self,
        *,
        task: TaskModel,
        gate_key: str,
        prompt: str,
        requested_by: str | None,
        expires_in_seconds: int | None,
        metadata: dict[str, Any],
    ) -> TaskApprovalModel:
        expires_at = (
            utc_now() + timedelta(seconds=expires_in_seconds)
            if expires_in_seconds is not None
            else None
        )

        row = TaskApprovalModel(
            task_id=task.id,
            workspace_id=task.workspace_id,
            gate_key=gate_key,
            status=ApprovalStatus.PENDING.value,
            prompt=prompt,
            requested_by=requested_by,
            expires_at=expires_at,
            metadata_json=metadata,
        )
        self.session.add(row)
        self.session.flush()

        TaskRepository(self.session).append_log(
            task.id,
            TaskLogCreate(
                level="WARNING",
                message="Task is waiting for user approval.",
                metadata={
                    "approval_id": row.id,
                    "gate_key": row.gate_key,
                    "prompt": row.prompt,
                    "expires_at": (
                        row.expires_at.isoformat()
                        if row.expires_at is not None
                        else None
                    ),
                },
            ),
        )
        return row

    def _expire_if_needed(
        self,
        row: TaskApprovalModel,
        task: TaskModel,
        *,
        now: datetime | None = None,
    ) -> None:
        if row.status != ApprovalStatus.PENDING.value:
            return

        expires_at = _as_aware(row.expires_at)
        current = now or utc_now()

        if expires_at is None or expires_at > current:
            return

        row.status = ApprovalStatus.EXPIRED.value
        row.decided_at = current
        row.decision_note = "Approval request expired."

        if task.status not in self.TERMINAL_TASK_STATUSES:
            TaskStateMachine.transition(task, TaskStatus.CANCELLED)

        TaskRepository(self.session).append_log(
            task.id,
            TaskLogCreate(
                level="WARNING",
                message="Task approval expired; Task cancelled.",
                metadata={
                    "approval_id": row.id,
                    "gate_key": row.gate_key,
                },
            ),
        )
        self.session.flush()

    @staticmethod
    def _move_task_to_waiting(task: TaskModel) -> None:
        if task.status == TaskStatus.WAITING.value:
            return

        if task.status in {
            TaskStatus.CREATED.value,
            TaskStatus.QUEUED.value,
        }:
            TaskStateMachine.transition(task, TaskStatus.WAITING)

    @staticmethod
    def serialize(row: TaskApprovalModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "task_id": row.task_id,
            "workspace_id": row.workspace_id,
            "gate_key": row.gate_key,
            "status": row.status,
            "prompt": row.prompt,
            "requested_by": row.requested_by,
            "requested_at": row.requested_at,
            "decided_by": row.decided_by,
            "decided_at": row.decided_at,
            "decision_note": row.decision_note,
            "expires_at": row.expires_at,
            "metadata": row.metadata_json,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }


class TaskApprovalManager:
    """Runtime approval coordinator that resumes or terminates workflows."""

    def __init__(
        self,
        *,
        event_bus: EventBus,
        workflow_engine: TaskWorkflowEngine,
        session_factory: SessionContextFactory = session_scope,
    ) -> None:
        self._event_bus = event_bus
        self._workflow_engine = workflow_engine
        self._session_factory = session_factory
        self._requested = 0
        self._approved = 0
        self._rejected = 0
        self._expired = 0

    def stats(self) -> dict[str, int]:
        return {
            "requested": self._requested,
            "approved": self._approved,
            "rejected": self._rejected,
            "expired": self._expired,
        }

    def get(self, approval_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            runtime = ApprovalGateRuntime(session)
            row = runtime.get(approval_id)

            if row is None:
                return None

            task = session.get(TaskModel, row.task_id)
            if task is not None:
                runtime._expire_if_needed(row, task)

            return runtime.serialize(row)

    def list(
        self,
        *,
        status: ApprovalStatus | None = None,
        workspace_id: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return ApprovalGateRuntime(session).list(
                status=status,
                workspace_id=workspace_id,
                task_id=task_id,
                limit=limit,
                offset=offset,
            )

    async def request(
        self,
        *,
        task_id: str,
        request: TaskApprovalRequest,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            task = session.get(TaskModel, task_id)

            if task is None:
                return None

            result = ApprovalGateRuntime(session).request(
                task,
                request,
            )

        self._requested += 1

        await self._event_bus.publish(
            Event(
                event_type="task.approval.requested",
                source="task_approval_manager",
                workspace_id=result["workspace_id"],
                payload=result,
            )
        )
        return result

    async def decide(
        self,
        *,
        approval_id: str,
        request: ApprovalDecisionRequest,
    ) -> dict[str, Any] | None:
        task_id: str
        workspace_id: str | None
        decision = request.decision
        result: dict[str, Any]

        with self._session_factory() as session:
            runtime = ApprovalGateRuntime(session)
            row = runtime.get(approval_id)

            if row is None:
                return None

            task = session.get(TaskModel, row.task_id)

            if task is None:
                raise ApprovalError("Task для Approval не найдена.")

            runtime._expire_if_needed(row, task)

            if row.status != ApprovalStatus.PENDING.value:
                raise ApprovalError(
                    "Approval уже завершён со статусом "
                    f"{row.status}."
                )

            row.decided_by = request.decided_by
            row.decided_at = utc_now()
            row.decision_note = request.note

            if decision == ApprovalDecision.APPROVE:
                row.status = ApprovalStatus.APPROVED.value
                TaskRepository(session).append_log(
                    task.id,
                    TaskLogCreate(
                        level="INFO",
                        message="Task execution approved by user.",
                        metadata={
                            "approval_id": row.id,
                            "gate_key": row.gate_key,
                            "decided_by": request.decided_by,
                            "note": request.note,
                        },
                    ),
                )
            else:
                row.status = ApprovalStatus.REJECTED.value

                if task.status not in ApprovalGateRuntime.TERMINAL_TASK_STATUSES:
                    TaskStateMachine.transition(
                        task,
                        TaskStatus.CANCELLED,
                    )

                TaskRepository(session).append_log(
                    task.id,
                    TaskLogCreate(
                        level="WARNING",
                        message="Task execution rejected by user.",
                        metadata={
                            "approval_id": row.id,
                            "gate_key": row.gate_key,
                            "decided_by": request.decided_by,
                            "note": request.note,
                        },
                    ),
                )

            session.flush()
            task_id = task.id
            workspace_id = task.workspace_id
            result = runtime.serialize(row)

        if decision == ApprovalDecision.APPROVE:
            self._approved += 1
            resume = await self._workflow_engine.enqueue_task(
                task_id,
                source="approval_approved",
            )
            result["resume"] = resume
            event_type = "task.approval.approved"
        else:
            self._rejected += 1
            propagation = await self._workflow_engine.handle_terminal_task(
                task_id
            )
            result["propagation"] = propagation
            event_type = "task.approval.rejected"

        await self._event_bus.publish(
            Event(
                event_type=event_type,
                source="task_approval_manager",
                workspace_id=workspace_id,
                payload=result,
            )
        )
        return result

    async def reconcile_expired(self) -> dict[str, Any]:
        expired_task_ids: list[str] = []
        expired_approvals: list[str] = []
        now = utc_now()

        with self._session_factory() as session:
            statement = select(TaskApprovalModel).where(
                TaskApprovalModel.status
                == ApprovalStatus.PENDING.value,
                TaskApprovalModel.expires_at.is_not(None),
                TaskApprovalModel.expires_at <= now,
            )
            runtime = ApprovalGateRuntime(session)

            for row in session.scalars(statement):
                task = session.get(TaskModel, row.task_id)

                if task is None:
                    row.status = ApprovalStatus.CANCELLED.value
                    row.decided_at = now
                    row.decision_note = "Related Task no longer exists."
                    continue

                runtime._expire_if_needed(row, task, now=now)

                if row.status == ApprovalStatus.EXPIRED.value:
                    expired_approvals.append(row.id)
                    expired_task_ids.append(task.id)

        for task_id in expired_task_ids:
            await self._workflow_engine.handle_terminal_task(task_id)

        self._expired += len(expired_approvals)

        result = {
            "expired_count": len(expired_approvals),
            "approval_ids": expired_approvals,
            "task_ids": expired_task_ids,
        }

        if expired_approvals:
            await self._event_bus.publish(
                Event(
                    event_type="task.approval.expired",
                    source="task_approval_manager",
                    payload=result,
                )
            )

        return result
