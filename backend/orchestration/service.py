from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError

from backend.core.events import Event, EventBus
from backend.orchestration.enums import ExecutionPlanStatus
from backend.orchestration.models import ExecutionPlanModel
from backend.orchestration.repository import ExecutionPlanRepository
from backend.orchestration.schemas import (
    ExecutionPlanCreate,
    ExecutionPlanStepCreate,
    ExecutionPlanStepUpdate,
    ExecutionPlanTransitionRequest,
    ExecutionPlanUpdate,
)
from backend.orchestration.state_machine import (
    ExecutionPlanStateMachine,
    InvalidExecutionPlanTransition,
)
from backend.orchestration.validator import ExecutionPlanValidator
from backend.task_engine.models import TaskModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ExecutionPlanError(ValueError):
    pass


class ExecutionPlanNotFound(ExecutionPlanError):
    pass


class ExecutionPlanService:
    EDITABLE_STATUSES = {
        ExecutionPlanStatus.DRAFT.value,
        ExecutionPlanStatus.VALIDATED.value,
        ExecutionPlanStatus.FAILED.value,
    }

    def __init__(
        self,
        repository: ExecutionPlanRepository,
        event_bus: EventBus,
    ) -> None:
        self._repository = repository
        self._event_bus = event_bus

    async def create_plan(
        self,
        request: ExecutionPlanCreate,
    ) -> dict[str, Any]:
        if request.source_task_id is not None:
            source_task = self._repository.session.get(
                TaskModel,
                request.source_task_id,
            )
            if source_task is None:
                raise ExecutionPlanError("Source Task не найдена.")
            if (
                request.workspace_id is not None
                and source_task.workspace_id != request.workspace_id
            ):
                raise ExecutionPlanError(
                    "Execution Plan и Source Task должны принадлежать "
                    "одному Workspace."
                )

        self._assert_unique_request_keys(request)

        try:
            row = self._repository.create(request)
        except IntegrityError as exc:
            raise ExecutionPlanError(
                "Нарушено ограничение уникальности Execution Plan."
            ) from exc

        payload = self._serialize_full(row)
        await self._publish(
            "execution_plan.created",
            row,
            {
                "plan_id": row.id,
                "source_task_id": row.source_task_id,
                "step_count": len(payload["steps"]),
                "status": row.status,
            },
        )
        return payload

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        row = self._repository.get_full(plan_id)
        return None if row is None else self._serialize_full(row)

    def list_plans(
        self,
        *,
        workspace_id: str | None = None,
        source_task_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        rows = self._repository.list(
            workspace_id=workspace_id,
            source_task_id=source_task_id,
            status=status,
            limit=limit,
            offset=offset,
        )
        return [self._serialize(row) for row in rows]

    async def update_plan(
        self,
        plan_id: str,
        request: ExecutionPlanUpdate,
    ) -> dict[str, Any] | None:
        row = self._repository.get(plan_id)
        if row is None:
            return None

        self._assert_editable(row)
        self._repository.update(row, request)
        self._invalidate(row)

        await self._publish(
            "execution_plan.updated",
            row,
            {"plan_id": row.id, "status": row.status},
        )
        return self._serialize_full(row)

    async def delete_plan(self, plan_id: str) -> bool:
        row = self._repository.get(plan_id)
        if row is None:
            return False
        if row.status != ExecutionPlanStatus.DRAFT.value:
            raise ExecutionPlanError(
                "Удалить можно только draft Execution Plan."
            )

        workspace_id = row.workspace_id
        self._repository.delete(row)
        await self._event_bus.publish(
            Event(
                event_type="execution_plan.deleted",
                source="execution_plan_service",
                workspace_id=workspace_id,
                payload={"plan_id": plan_id},
            )
        )
        return True

    async def add_step(
        self,
        plan_id: str,
        request: ExecutionPlanStepCreate,
    ) -> dict[str, Any] | None:
        plan = self._repository.get(plan_id)
        if plan is None:
            return None

        self._assert_editable(plan)
        if self._repository.get_step_by_key(plan_id, request.step_key):
            raise ExecutionPlanError(
                f"Шаг с ключом {request.step_key} уже существует."
            )

        try:
            step = self._repository.add_step(plan_id, request)
        except IntegrityError as exc:
            raise ExecutionPlanError(
                f"Шаг с ключом {request.step_key} уже существует."
            ) from exc

        self._invalidate(plan)
        await self._publish(
            "execution_plan.step.created",
            plan,
            {
                "plan_id": plan.id,
                "step_id": step.id,
                "step_key": step.step_key,
            },
        )
        return self._serialize_full(plan)

    async def update_step(
        self,
        plan_id: str,
        step_id: str,
        request: ExecutionPlanStepUpdate,
    ) -> dict[str, Any] | None:
        plan = self._repository.get(plan_id)
        if plan is None:
            return None

        self._assert_editable(plan)
        step = self._repository.get_step(plan_id, step_id)
        if step is None:
            raise ExecutionPlanNotFound("Execution Plan Step не найден.")

        values = request.model_dump(exclude_unset=True)
        new_key = values.get("step_key")

        if new_key and new_key != step.step_key:
            duplicate = self._repository.get_step_by_key(plan_id, new_key)
            if duplicate is not None:
                raise ExecutionPlanError(
                    f"Шаг с ключом {new_key} уже существует."
                )

            old_key = step.step_key
            for sibling in self._repository.list_steps(plan_id):
                if old_key in (sibling.depends_on_json or []):
                    sibling.depends_on_json = [
                        new_key if item == old_key else item
                        for item in sibling.depends_on_json
                    ]

        assignment_sensitive_fields = {
            "step_type",
            "agent_role",
            "capability",
            "tool_name",
            "metadata",
        }
        if assignment_sensitive_fields & set(values):
            step.assigned_agent_id = None
            step.assignment_json = {}
            step.assigned_at = None

        self._repository.update_step(step, request)
        self._invalidate(plan)

        await self._publish(
            "execution_plan.step.updated",
            plan,
            {
                "plan_id": plan.id,
                "step_id": step.id,
                "step_key": step.step_key,
            },
        )
        return self._serialize_full(plan)

    async def delete_step(
        self,
        plan_id: str,
        step_id: str,
    ) -> dict[str, Any] | None:
        plan = self._repository.get(plan_id)
        if plan is None:
            return None

        self._assert_editable(plan)
        step = self._repository.get_step(plan_id, step_id)
        if step is None:
            raise ExecutionPlanNotFound("Execution Plan Step не найден.")

        dependents = [
            sibling.step_key
            for sibling in self._repository.list_steps(plan_id)
            if step.step_key in (sibling.depends_on_json or [])
        ]
        if dependents:
            raise ExecutionPlanError(
                "Нельзя удалить шаг, от которого зависят: "
                + ", ".join(sorted(dependents))
                + "."
            )

        deleted_key = step.step_key
        self._repository.delete_step(step)
        self._invalidate(plan)

        await self._publish(
            "execution_plan.step.deleted",
            plan,
            {
                "plan_id": plan.id,
                "step_id": step_id,
                "step_key": deleted_key,
            },
        )
        return self._serialize_full(plan)

    async def validate_plan(
        self,
        plan_id: str,
    ) -> dict[str, Any] | None:
        plan = self._repository.get(plan_id)
        if plan is None:
            return None
        if plan.status == ExecutionPlanStatus.RUNNING.value:
            raise ExecutionPlanError(
                "Нельзя повторно валидировать выполняющийся Plan."
            )
        if plan.status in {
            ExecutionPlanStatus.COMPLETED.value,
            ExecutionPlanStatus.CANCELLED.value,
            ExecutionPlanStatus.SUPERSEDED.value,
        }:
            raise ExecutionPlanError(
                "Нельзя валидировать завершённый Execution Plan."
            )

        result = ExecutionPlanValidator.validate(
            self._repository.list_steps(plan.id),
            max_parallel_steps=plan.max_parallel_steps,
        )
        plan.validation_json = result

        if result["valid"]:
            plan.status = ExecutionPlanStatus.VALIDATED.value
            plan.validated_at = utc_now()
        else:
            plan.status = ExecutionPlanStatus.DRAFT.value
            plan.validated_at = None

        self._repository.session.flush()
        await self._publish(
            "execution_plan.validated"
            if result["valid"]
            else "execution_plan.validation_failed",
            plan,
            {
                "plan_id": plan.id,
                "valid": result["valid"],
                "errors": result["errors"],
                "warnings": result["warnings"],
            },
        )
        return self._serialize_full(plan)

    async def transition_plan(
        self,
        plan_id: str,
        request: ExecutionPlanTransitionRequest,
    ) -> dict[str, Any] | None:
        plan = self._repository.get(plan_id)
        if plan is None:
            return None

        if request.status == ExecutionPlanStatus.VALIDATED:
            return await self.validate_plan(plan_id)

        if request.status == ExecutionPlanStatus.READY:
            validation = plan.validation_json or {}
            if not validation.get("valid"):
                raise ExecutionPlanError(
                    "Execution Plan необходимо успешно валидировать."
                )

        previous, current = ExecutionPlanStateMachine.transition(
            plan,
            request.status,
        )
        self._repository.session.flush()

        await self._publish(
            f"execution_plan.{current.value}",
            plan,
            {
                "plan_id": plan.id,
                "previous_status": previous.value,
                "status": current.value,
                "reason": request.reason,
            },
        )
        return self._serialize_full(plan)

    def graph(self, plan_id: str) -> dict[str, Any] | None:
        plan = self._repository.get(plan_id)
        if plan is None:
            return None

        steps = self._repository.list_steps(plan_id)
        validation = ExecutionPlanValidator.validate(
            steps,
            max_parallel_steps=plan.max_parallel_steps,
        )
        return {
            "plan_id": plan.id,
            "status": plan.status,
            "valid": validation["valid"],
            "errors": validation["errors"],
            "warnings": validation["warnings"],
            "topological_order": validation["topological_order"],
            "parallel_groups": validation["parallel_groups"],
            "roots": validation["roots"],
            "leaves": validation["leaves"],
            "nodes": [
                {
                    "step_id": step.id,
                    "step_key": step.step_key,
                    "title": step.title,
                    "step_type": step.step_type,
                    "status": step.status,
                    "agent_role": step.agent_role,
                    "capability": step.capability,
                    "tool_name": step.tool_name,
                    "assigned_agent_id": step.assigned_agent_id,
                    "assignment": step.assignment_json,
                    "assigned_at": step.assigned_at,
                    "sequence": step.sequence,
                }
                for step in steps
            ],
            "edges": [
                {
                    "from": dependency,
                    "to": step.step_key,
                }
                for step in steps
                for dependency in (step.depends_on_json or [])
            ],
        }

    def _assert_editable(self, plan: ExecutionPlanModel) -> None:
        if plan.status not in self.EDITABLE_STATUSES:
            raise ExecutionPlanError(
                "Редактирование запрещено для Execution Plan "
                f"в статусе {plan.status}."
            )

    @staticmethod
    def _assert_unique_request_keys(
        request: ExecutionPlanCreate,
    ) -> None:
        keys = [step.step_key for step in request.steps]
        duplicates = sorted(
            key for key in set(keys) if keys.count(key) > 1
        )
        if duplicates:
            raise ExecutionPlanError(
                "Повторяющиеся step_key: "
                + ", ".join(duplicates)
                + "."
            )

    def _invalidate(self, plan: ExecutionPlanModel) -> None:
        plan.status = ExecutionPlanStatus.DRAFT.value
        plan.validation_json = {}
        plan.validated_at = None
        plan.started_at = None
        plan.finished_at = None
        self._repository.session.flush()

    async def _publish(
        self,
        event_type: str,
        plan: ExecutionPlanModel,
        payload: dict[str, Any],
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type=event_type,
                source="execution_plan_service",
                workspace_id=plan.workspace_id,
                correlation_id=plan.id,
                payload=payload,
            )
        )

    def _serialize_full(
        self,
        row: ExecutionPlanModel,
    ) -> dict[str, Any]:
        payload = self._serialize(row)
        payload["steps"] = [
            self._serialize_step(step)
            for step in self._repository.list_steps(row.id)
        ]
        return payload

    @staticmethod
    def _serialize(row: ExecutionPlanModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "source_task_id": row.source_task_id,
            "title": row.title,
            "objective": row.objective,
            "strategy": row.strategy,
            "status": row.status,
            "version": row.version,
            "max_parallel_steps": row.max_parallel_steps,
            "planner": row.planner,
            "validation": row.validation_json,
            "metadata": row.metadata_json,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "validated_at": row.validated_at,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
        }

    @staticmethod
    def _serialize_step(row) -> dict[str, Any]:
        return {
            "id": row.id,
            "plan_id": row.plan_id,
            "step_key": row.step_key,
            "sequence": row.sequence,
            "step_type": row.step_type,
            "status": row.status,
            "title": row.title,
            "description": row.description,
            "agent_role": row.agent_role,
            "capability": row.capability,
            "tool_name": row.tool_name,
            "input": row.input_json,
            "output": row.output_json,
            "depends_on": row.depends_on_json,
            "condition": row.condition_json,
            "timeout_seconds": row.timeout_seconds,
            "max_retries": row.max_retries,
            "metadata": row.metadata_json,
            "assigned_agent_id": row.assigned_agent_id,
            "assignment": row.assignment_json,
            "assigned_at": row.assigned_at,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
        }


__all__ = [
    "ExecutionPlanError",
    "ExecutionPlanNotFound",
    "ExecutionPlanService",
    "InvalidExecutionPlanTransition",
]
