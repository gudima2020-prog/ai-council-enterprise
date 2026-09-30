from __future__ import annotations

from typing import Any

from backend.task_engine.admission import TaskAdmissionManager
from backend.task_engine.enums import TaskType
from backend.task_engine.executor import (
    TaskExecutionContext,
    TaskExecutor,
)
from backend.task_engine.queue import QueueItem
from backend.task_engine.resource_locks import ResourceLockManager
from backend.task_engine.workflow import TaskWorkflowEngine


DEFAULT_TYPE_LIMITS = {
    TaskType.SYSTEM.value: 4,
    TaskType.PLUGIN.value: 2,
    TaskType.WORKFLOW.value: 1,
    TaskType.TRADER.value: 1,
    TaskType.WORKER.value: 2,
    TaskType.CHAT.value: 4,
    TaskType.IMPORT.value: 1,
    TaskType.EXPORT.value: 2,
    TaskType.PIPELINE.value: 2,
}


class ParallelTaskExecutor(TaskExecutor):
    def __init__(
        self,
        *,
        workflow_engine: TaskWorkflowEngine | None = None,
        admission_manager: TaskAdmissionManager | None = None,
        type_limits: dict[str, int] | None = None,
        resource_capacities: dict[str, int] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._workflow_engine = workflow_engine
        self._admission_manager = admission_manager

        limits = {
            **DEFAULT_TYPE_LIMITS,
            **(type_limits or {}),
        }
        capacities = {
            f"type:{task_type.strip().lower()}": capacity
            for task_type, capacity in limits.items()
        }
        capacities.update(resource_capacities or {})
        self._parallel_resource_locks = ResourceLockManager(capacities)

    @property
    def resource_locks(self) -> ResourceLockManager:
        return self._parallel_resource_locks

    def stats(self) -> dict[str, Any]:
        return {
            **super().stats(),
            "resource_locks": self._parallel_resource_locks.stats()[
                "resources"
            ],
        }

    async def execute_queue_item(self, item: QueueItem) -> None:
        if self._workflow_engine is not None:
            readiness = (
                await self._workflow_engine.ensure_ready_for_execution(
                    item.task_id
                )
            )
            if readiness is None or not readiness["ready"]:
                return

        if self._admission_manager is not None:
            governance_valid = self._governance_preflight_is_valid(
                item.task_id
            )
            if governance_valid is False:
                await super().execute_queue_item(item)
                if self._workflow_engine is not None:
                    await self._workflow_engine.handle_terminal_task(
                        item.task_id
                    )
                return

            admission = await self._admission_manager.evaluate_task(
                item.task_id,
                reserve=True,
                actor_id="task_executor",
                source="executor_preflight",
            )
            if admission is None or not admission["allowed"]:
                return

        await super().execute_queue_item(item)

        if self._workflow_engine is not None:
            await self._workflow_engine.handle_terminal_task(
                item.task_id
            )

    async def _execute_claimed(
        self,
        context: TaskExecutionContext,
        run_id: str,
    ) -> None:
        execution = context.payload.get("_execution", {})
        if not isinstance(execution, dict):
            execution = {}

        raw_resources = execution.get("resource_locks", [])
        if not isinstance(raw_resources, list):
            raw_resources = []

        resources = [
            f"type:{context.task_type}",
            *[
                str(item).strip().lower()
                for item in raw_resources
                if str(item).strip()
            ],
        ]

        concurrency_key = str(
            execution.get("concurrency_key", "")
        ).strip().lower()
        if concurrency_key:
            resources.append(f"key:{concurrency_key}")

        async with self._parallel_resource_locks.acquire_many(resources):
            await super()._execute_claimed(context, run_id)
