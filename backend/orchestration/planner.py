from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.events import Event, EventBus
from backend.database.session import session_scope
from backend.orchestration.agent_schemas import AgentAssignmentRequest
from backend.orchestration.agents import (
    AgentAssignmentService,
    AgentRegistryError,
    AgentRepository,
)
from backend.orchestration.enums import (
    ExecutionPlanStatus,
    ExecutionStepStatus,
    ExecutionStepType,
)
from backend.orchestration.models import (
    ExecutionPlannerRunModel,
    ExecutionPlanModel,
)
from backend.orchestration.planner_schemas import (
    ExecutionPlanGenerationRequest,
    ExecutionPlanReplanRequest,
    PlannerMode,
    PlannerRunStatus,
    PlannerRunType,
)
from backend.orchestration.repository import ExecutionPlanRepository
from backend.orchestration.runtime import ExecutionPlanRuntime
from backend.orchestration.runtime_schemas import ExecutionPlanRunRequest
from backend.orchestration.schemas import (
    ExecutionPlanCreate,
    ExecutionPlanStepCreate,
)
from backend.orchestration.service import (
    ExecutionPlanError,
    ExecutionPlanService,
)
from backend.orchestration.state_machine import ExecutionPlanStateMachine
from backend.task_engine.models import TaskModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


SessionContextFactory = Callable[[], AbstractContextManager[Session]]


class ExecutionPlannerError(RuntimeError):
    pass


class PlannerAdapterNotRegistered(ExecutionPlannerError):
    pass


@dataclass(frozen=True)
class PlannerContext:
    run_id: str
    run_type: PlannerRunType
    request: ExecutionPlanGenerationRequest | ExecutionPlanReplanRequest
    source_task: dict[str, Any] | None
    previous_plan: dict[str, Any] | None
    failure_context: dict[str, Any]
    lineage: dict[str, Any]


class PlannerAdapter(Protocol):
    async def __call__(self, context: PlannerContext) -> ExecutionPlanCreate:
        ...


class PlannerAdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, PlannerAdapter] = {}

    def register(
        self,
        planner_ref: str,
        adapter: PlannerAdapter,
        *,
        replace: bool = False,
    ) -> None:
        key = self._normalize(planner_ref)
        if key in self._adapters and not replace:
            raise ValueError(f"Planner Adapter уже зарегистрирован: {key}.")
        self._adapters[key] = adapter

    def get(self, planner_ref: str) -> PlannerAdapter:
        key = self._normalize(planner_ref)
        adapter = self._adapters.get(key)
        if adapter is None:
            raise PlannerAdapterNotRegistered(
                f"Planner Adapter не зарегистрирован: {planner_ref}."
            )
        return adapter

    def diagnostics(self) -> list[str]:
        return sorted(self._adapters)

    @staticmethod
    def _normalize(value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("planner_ref не может быть пустым.")
        return normalized


class PlannerRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        run_type: PlannerRunType,
        planner_ref: str,
        workspace_id: str | None,
        source_task_id: str | None,
        source_plan_id: str | None,
        request_json: dict[str, Any],
        failure_context: dict[str, Any] | None = None,
    ) -> ExecutionPlannerRunModel:
        row = ExecutionPlannerRunModel(
            run_type=run_type.value,
            status=PlannerRunStatus.RUNNING.value,
            planner_ref=planner_ref,
            workspace_id=workspace_id,
            source_task_id=source_task_id,
            source_plan_id=source_plan_id,
            request_json=request_json,
            failure_context_json=failure_context or {},
            started_at=utc_now(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get(self, run_id: str) -> ExecutionPlannerRunModel | None:
        return self.session.get(ExecutionPlannerRunModel, run_id)

    def list(
        self,
        *,
        workspace_id: str | None = None,
        source_task_id: str | None = None,
        source_plan_id: str | None = None,
        run_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ExecutionPlannerRunModel]:
        statement = select(ExecutionPlannerRunModel)

        if workspace_id is not None:
            statement = statement.where(
                ExecutionPlannerRunModel.workspace_id == workspace_id
            )
        if source_task_id is not None:
            statement = statement.where(
                ExecutionPlannerRunModel.source_task_id == source_task_id
            )
        if source_plan_id is not None:
            statement = statement.where(
                ExecutionPlannerRunModel.source_plan_id == source_plan_id
            )
        if run_type is not None:
            statement = statement.where(
                ExecutionPlannerRunModel.run_type == run_type
            )
        if status is not None:
            statement = statement.where(
                ExecutionPlannerRunModel.status == status
            )

        statement = (
            statement
            .order_by(ExecutionPlannerRunModel.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self.session.scalars(statement).all())

    def count_replans_for_root(self, root_plan_id: str) -> int:
        statement = select(ExecutionPlannerRunModel).where(
            ExecutionPlannerRunModel.run_type == PlannerRunType.REPLAN.value,
            ExecutionPlannerRunModel.status == PlannerRunStatus.COMPLETED.value,
        )
        return sum(
            1
            for row in self.session.scalars(statement).all()
            if str((row.request_json or {}).get("root_plan_id") or "")
            == root_plan_id
        )

    def complete(
        self,
        row: ExecutionPlannerRunModel,
        *,
        result_plan_id: str,
        response_json: dict[str, Any],
        duration_ms: float,
    ) -> None:
        row.status = PlannerRunStatus.COMPLETED.value
        row.result_plan_id = result_plan_id
        row.response_json = response_json
        row.finished_at = utc_now()
        row.duration_ms = duration_ms
        row.error = None
        self.session.flush()

    def fail(
        self,
        row: ExecutionPlannerRunModel,
        *,
        error: str,
        duration_ms: float,
    ) -> None:
        row.status = PlannerRunStatus.FAILED.value
        row.error = error
        row.finished_at = utc_now()
        row.duration_ms = duration_ms
        self.session.flush()


class BuiltinPlannerAdapter:
    async def __call__(self, context: PlannerContext) -> ExecutionPlanCreate:
        if context.run_type == PlannerRunType.REPLAN:
            return self._replan(context)
        return self._generate(context)

    def _generate(self, context: PlannerContext) -> ExecutionPlanCreate:
        request = context.request
        assert isinstance(request, ExecutionPlanGenerationRequest)

        source = context.source_task or {}
        objective = (
            (request.objective or "").strip()
            or str(source.get("description") or "").strip()
            or str(source.get("title") or "").strip()
        )
        title = (
            (request.title or "").strip()
            or f"План выполнения: {source.get('title') or objective[:80]}"
        )
        common_input = {
            "objective": objective,
            "source_task": source,
            "planner_context": deepcopy(request.context),
        }

        steps = self._steps_for_mode(
            request.mode,
            common_input=common_input,
        )

        metadata = {
            "_planner": {
                "run_id": context.run_id,
                "planner_ref": request.planner_ref,
                "mode": request.mode.value,
                "revision": 1,
                "root_plan_id": None,
                "parent_plan_id": None,
                "auto_replan": request.auto_replan,
                "max_replans": request.max_replans,
            },
            "planner_context": deepcopy(request.context),
            "_review_policy": {
                "enabled": request.auto_review,
                "reviewer_ref": "builtin.critic",
                "threshold": request.review_threshold,
                "auto_fix": request.auto_fix_review,
                "max_rounds": request.max_review_rounds,
                "require_pass": request.require_review_pass,
            },
        }

        strategy = request.strategy_hint.strip() or self._strategy_for_mode(
            request.mode
        )

        return ExecutionPlanCreate(
            workspace_id=request.workspace_id or source.get("workspace_id"),
            source_task_id=request.source_task_id,
            title=title,
            objective=objective,
            strategy=strategy,
            version=1,
            max_parallel_steps=request.max_parallel_steps,
            planner=request.planner_ref,
            metadata=metadata,
            steps=steps,
        )

    def _replan(self, context: PlannerContext) -> ExecutionPlanCreate:
        request = context.request
        assert isinstance(request, ExecutionPlanReplanRequest)
        previous = context.previous_plan or {}
        previous_steps = list(previous.get("steps") or [])
        completed_statuses = {
            ExecutionStepStatus.COMPLETED.value,
            ExecutionStepStatus.SKIPPED.value,
        }
        steps: list[ExecutionPlanStepCreate] = []

        for raw in previous_steps:
            status = str(raw.get("status") or "")
            key = str(raw["step_key"])
            metadata = deepcopy(raw.get("metadata") or {})
            metadata["replanned_from_step_id"] = raw.get("id")
            metadata["replanned_from_status"] = status

            if request.preserve_completed_outputs and status in completed_statuses:
                steps.append(
                    ExecutionPlanStepCreate(
                        step_key=key,
                        sequence=int(raw.get("sequence") or 0),
                        step_type=ExecutionStepType.CHECKPOINT,
                        title=f"Сохранённый результат: {raw.get('title') or key}",
                        description=(
                            "Результат перенесён из предыдущей версии плана."
                        ),
                        input={
                            "_preserved_output": deepcopy(raw.get("output")),
                            "source_plan_id": previous.get("id"),
                            "source_step_id": raw.get("id"),
                        },
                        depends_on=list(raw.get("depends_on") or []),
                        timeout_seconds=30,
                        max_retries=0,
                        metadata=metadata,
                    )
                )
                continue

            failed = status == ExecutionStepStatus.FAILED.value
            timeout_seconds = int(raw.get("timeout_seconds") or 300)
            max_retries = int(raw.get("max_retries") or 0)

            if failed:
                timeout_seconds = min(max(timeout_seconds + 30, int(timeout_seconds * 1.5)), 86_400)
                max_retries = min(max_retries + 1, 100)
                metadata["failure_context"] = deepcopy(context.failure_context)
                metadata["recovery_action"] = "retry_with_adjusted_limits"

            steps.append(
                ExecutionPlanStepCreate(
                    step_key=key,
                    sequence=int(raw.get("sequence") or 0),
                    step_type=ExecutionStepType(str(raw.get("step_type"))),
                    title=str(raw.get("title") or key),
                    description=str(raw.get("description") or ""),
                    agent_role=raw.get("agent_role"),
                    capability=raw.get("capability"),
                    tool_name=raw.get("tool_name"),
                    input=deepcopy(raw.get("input") or {}),
                    depends_on=list(raw.get("depends_on") or []),
                    condition=deepcopy(raw.get("condition")),
                    timeout_seconds=timeout_seconds,
                    max_retries=max_retries,
                    metadata=metadata,
                )
            )

        revision = int(context.lineage["revision"])
        planner_ref = str(context.lineage["planner_ref"])
        mode = str(context.lineage["mode"])
        strategy = str(previous.get("strategy") or "").strip()
        strategy = (
            strategy
            + "\n\nReplanning: "
            + request.reason.strip()
        ).strip()

        metadata = deepcopy(previous.get("metadata") or {})
        metadata["_planner"] = {
            **dict(metadata.get("_planner") or {}),
            "run_id": context.run_id,
            "planner_ref": planner_ref,
            "mode": mode,
            "revision": revision,
            "root_plan_id": context.lineage["root_plan_id"],
            "parent_plan_id": previous.get("id"),
            "last_replan_reason": request.reason,
        }
        metadata["failure_context"] = deepcopy(context.failure_context)
        metadata["replan_context"] = deepcopy(request.context)
        metadata["_review_policy"] = {
            "enabled": request.auto_review,
            "reviewer_ref": "builtin.critic",
            "threshold": request.review_threshold,
            "auto_fix": request.auto_fix_review,
            "max_rounds": request.max_review_rounds,
            "require_pass": request.require_review_pass,
        }

        return ExecutionPlanCreate(
            workspace_id=previous.get("workspace_id"),
            source_task_id=previous.get("source_task_id"),
            title=(f"{previous.get('title') or 'Execution Plan'} — revision {revision}")[:255],
            objective=str(previous.get("objective") or ""),
            strategy=strategy,
            version=revision,
            max_parallel_steps=int(
                previous.get("max_parallel_steps") or 4
            ),
            planner=planner_ref,
            metadata=metadata,
            steps=steps,
        )

    @staticmethod
    def _strategy_for_mode(mode: PlannerMode) -> str:
        if mode == PlannerMode.FAST:
            return "Быстрый анализ, выполнение и итоговая проверка."
        if mode == PlannerMode.THOROUGH:
            return (
                "Расширенный анализ, параллельная оценка рисков и фактов, "
                "проектирование, выполнение, независимая проверка и отчёт."
            )
        return (
            "Последовательный анализ, проектирование решения, выполнение, "
            "проверка и подготовка результата."
        )

    @staticmethod
    def _steps_for_mode(
        mode: PlannerMode,
        *,
        common_input: dict[str, Any],
    ) -> list[ExecutionPlanStepCreate]:
        analyze = ExecutionPlanStepCreate(
            step_key="analyze",
            sequence=10,
            step_type=ExecutionStepType.AGENT,
            title="Проанализировать задачу",
            agent_role="analyst",
            capability="task_analysis",
            input=deepcopy(common_input),
            timeout_seconds=300,
            max_retries=1,
        )

        execute = ExecutionPlanStepCreate(
            step_key="execute",
            sequence=40,
            step_type=ExecutionStepType.AGENT,
            title="Выполнить задачу",
            agent_role="worker",
            capability="task_execution",
            input={
                **deepcopy(common_input),
                "analysis": "$steps.analyze.output",
            },
            depends_on=["analyze"],
            timeout_seconds=900,
            max_retries=1,
        )

        deliver = ExecutionPlanStepCreate(
            step_key="deliver",
            sequence=90,
            step_type=ExecutionStepType.AGENT,
            title="Подготовить итоговый результат",
            agent_role="writer",
            capability="report_generation",
            input={
                **deepcopy(common_input),
                "execution": "$steps.execute.output",
            },
            depends_on=["execute"],
            timeout_seconds=300,
            max_retries=1,
        )

        if mode == PlannerMode.FAST:
            return [analyze, execute, deliver]

        design = ExecutionPlanStepCreate(
            step_key="design",
            sequence=30,
            step_type=ExecutionStepType.AGENT,
            title="Спроектировать способ выполнения",
            agent_role="planner",
            capability="execution_plan_design",
            input={
                **deepcopy(common_input),
                "analysis": "$steps.analyze.output",
            },
            depends_on=["analyze"],
            timeout_seconds=300,
            max_retries=1,
        )
        execute.depends_on = ["design"]
        execute.input["design"] = "$steps.design.output"

        verify = ExecutionPlanStepCreate(
            step_key="verify",
            sequence=70,
            step_type=ExecutionStepType.AGENT,
            title="Проверить результат",
            agent_role="analyst",
            capability="analysis",
            input={
                **deepcopy(common_input),
                "execution": "$steps.execute.output",
            },
            depends_on=["execute"],
            timeout_seconds=300,
            max_retries=1,
        )
        deliver.depends_on = ["verify"]
        deliver.input["verification"] = "$steps.verify.output"

        if mode == PlannerMode.BALANCED:
            return [analyze, design, execute, verify, deliver]

        research = ExecutionPlanStepCreate(
            step_key="research",
            sequence=20,
            step_type=ExecutionStepType.AGENT,
            title="Собрать дополнительные факты",
            agent_role="analyst",
            capability="research",
            input=deepcopy(common_input),
            depends_on=["analyze"],
            timeout_seconds=600,
            max_retries=1,
        )
        risk = ExecutionPlanStepCreate(
            step_key="risk",
            sequence=20,
            step_type=ExecutionStepType.AGENT,
            title="Оценить риски и ограничения",
            agent_role="analyst",
            capability="analysis",
            input=deepcopy(common_input),
            depends_on=["analyze"],
            timeout_seconds=300,
            max_retries=1,
        )
        design.depends_on = ["research", "risk"]
        design.input["research"] = "$steps.research.output"
        design.input["risk"] = "$steps.risk.output"

        return [analyze, research, risk, design, execute, verify, deliver]


class ExecutionPlannerService:
    def __init__(
        self,
        *,
        event_bus: EventBus,
        session_factory: SessionContextFactory = session_scope,
        runtime: ExecutionPlanRuntime | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._runtime = runtime
        self._registry = PlannerAdapterRegistry()
        self._registry.register("builtin.planner", BuiltinPlannerAdapter())
        self._active: dict[str, asyncio.Task[Any]] = {}
        self._generated = 0
        self._replanned = 0
        self._failed = 0
        self._auto_replanned = 0

    @property
    def registry(self) -> PlannerAdapterRegistry:
        return self._registry

    def stats(self) -> dict[str, Any]:
        return {
            "active_run_ids": sorted(self._active),
            "active_count": len(self._active),
            "generated": self._generated,
            "replanned": self._replanned,
            "auto_replanned": self._auto_replanned,
            "failed": self._failed,
            "planner_adapters": self._registry.diagnostics(),
        }

    async def generate(
        self,
        request: ExecutionPlanGenerationRequest,
    ) -> dict[str, Any]:
        source_task = self._load_source_task(request.source_task_id)
        workspace_id = request.workspace_id

        if source_task is not None:
            if workspace_id is not None and workspace_id != source_task["workspace_id"]:
                raise ExecutionPlannerError(
                    "Planner request и Source Task должны принадлежать одному Workspace."
                )
            workspace_id = source_task["workspace_id"]

        request_data = request.model_dump(mode="json")
        request_data["workspace_id"] = workspace_id
        run_id = self._create_run(
            run_type=PlannerRunType.GENERATE,
            planner_ref=request.planner_ref,
            workspace_id=workspace_id,
            source_task_id=request.source_task_id,
            source_plan_id=None,
            request_json=request_data,
            failure_context={},
        )
        started = perf_counter()

        try:
            adapter = self._registry.get(request.planner_ref)
            draft = await adapter(
                PlannerContext(
                    run_id=run_id,
                    run_type=PlannerRunType.GENERATE,
                    request=request.model_copy(update={"workspace_id": workspace_id}),
                    source_task=source_task,
                    previous_plan=None,
                    failure_context={},
                    lineage={
                        "root_plan_id": None,
                        "revision": 1,
                        "planner_ref": request.planner_ref,
                        "mode": request.mode.value,
                    },
                )
            )
            plan = await self._persist_plan(
                draft,
                run_id=run_id,
                auto_validate=request.auto_validate,
                auto_assign=request.auto_assign,
                strict_assignment=request.strict_assignment,
                root_plan_id=None,
            )
            duration_ms = (perf_counter() - started) * 1000
            self._finish_run_success(
                run_id,
                result_plan_id=plan["id"],
                response_json={
                    "plan_id": plan["id"],
                    "status": plan["status"],
                    "step_count": len(plan["steps"]),
                },
                duration_ms=duration_ms,
            )
            self._generated += 1

            runtime_status = None
            if request.auto_start:
                runtime_status = await self._start_plan(
                    plan["id"],
                    wait=request.wait,
                    wait_timeout_seconds=request.wait_timeout_seconds,
                )

            await self._event_bus.publish(
                Event(
                    event_type="execution_plan.planner.completed",
                    source="execution_planner_service",
                    workspace_id=workspace_id,
                    correlation_id=plan["id"],
                    payload={
                        "run_id": run_id,
                        "plan_id": plan["id"],
                        "run_type": PlannerRunType.GENERATE.value,
                        "duration_ms": duration_ms,
                    },
                )
            )
            return {
                "planner_run": self.get_run(run_id),
                "plan": plan,
                "runtime": runtime_status,
            }
        except Exception as exc:
            self._failed += 1
            duration_ms = (perf_counter() - started) * 1000
            self._finish_run_failure(
                run_id,
                error=str(exc),
                duration_ms=duration_ms,
            )
            await self._publish_failure(
                run_id=run_id,
                workspace_id=workspace_id,
                run_type=PlannerRunType.GENERATE,
                exc=exc,
            )
            if isinstance(exc, ExecutionPlannerError):
                raise
            raise ExecutionPlannerError(str(exc)) from exc

    async def replan(
        self,
        plan_id: str,
        request: ExecutionPlanReplanRequest,
        *,
        automatic: bool = False,
    ) -> dict[str, Any] | None:
        previous = self._load_plan(plan_id)
        if previous is None:
            return None

        if previous["status"] not in {
            ExecutionPlanStatus.FAILED.value,
            ExecutionPlanStatus.CANCELLED.value,
        } and not request.force:
            raise ExecutionPlannerError(
                "Replanning разрешён для failed/cancelled Plan или с force=true."
            )

        planner_meta = dict((previous.get("metadata") or {}).get("_planner") or {})
        planner_ref = (
            (request.planner_ref or "").strip()
            or str(planner_meta.get("planner_ref") or previous.get("planner") or "builtin.planner")
        )
        mode = request.mode or PlannerMode(
            str(planner_meta.get("mode") or PlannerMode.BALANCED.value)
        )
        root_plan_id = str(planner_meta.get("root_plan_id") or previous["id"])
        revision = int(planner_meta.get("revision") or previous.get("version") or 1) + 1
        max_replans = int(planner_meta.get("max_replans") or 0)

        if automatic:
            completed_replans = self._count_replans(root_plan_id)
            if completed_replans >= max_replans:
                raise ExecutionPlannerError(
                    "Автоматический лимит replanning исчерпан."
                )

        failure_context = self._build_failure_context(
            previous,
            reason=request.reason,
            failed_step_id=request.failed_step_id,
        )
        request_data = request.model_dump(mode="json")
        request_data.update(
            {
                "root_plan_id": root_plan_id,
                "revision": revision,
                "automatic": automatic,
            }
        )
        run_id = self._create_run(
            run_type=PlannerRunType.REPLAN,
            planner_ref=planner_ref,
            workspace_id=previous.get("workspace_id"),
            source_task_id=previous.get("source_task_id"),
            source_plan_id=previous["id"],
            request_json=request_data,
            failure_context=failure_context,
        )
        started = perf_counter()

        try:
            adapter = self._registry.get(planner_ref)
            draft = await adapter(
                PlannerContext(
                    run_id=run_id,
                    run_type=PlannerRunType.REPLAN,
                    request=request,
                    source_task=self._load_source_task(previous.get("source_task_id")),
                    previous_plan=previous,
                    failure_context=failure_context,
                    lineage={
                        "root_plan_id": root_plan_id,
                        "revision": revision,
                        "planner_ref": planner_ref,
                        "mode": mode.value,
                    },
                )
            )
            plan = await self._persist_plan(
                draft,
                run_id=run_id,
                auto_validate=request.auto_validate,
                auto_assign=request.auto_assign,
                strict_assignment=request.strict_assignment,
                root_plan_id=root_plan_id,
            )
            self._supersede_plan(previous["id"], plan["id"])
            duration_ms = (perf_counter() - started) * 1000
            self._finish_run_success(
                run_id,
                result_plan_id=plan["id"],
                response_json={
                    "plan_id": plan["id"],
                    "source_plan_id": previous["id"],
                    "root_plan_id": root_plan_id,
                    "revision": revision,
                    "status": plan["status"],
                },
                duration_ms=duration_ms,
            )
            self._replanned += 1
            if automatic:
                self._auto_replanned += 1

            runtime_status = None
            if request.auto_start:
                runtime_status = await self._start_plan(
                    plan["id"],
                    wait=request.wait,
                    wait_timeout_seconds=request.wait_timeout_seconds,
                )

            await self._event_bus.publish(
                Event(
                    event_type="execution_plan.planner.replanned",
                    source="execution_planner_service",
                    workspace_id=previous.get("workspace_id"),
                    correlation_id=plan["id"],
                    payload={
                        "run_id": run_id,
                        "source_plan_id": previous["id"],
                        "result_plan_id": plan["id"],
                        "root_plan_id": root_plan_id,
                        "revision": revision,
                        "automatic": automatic,
                        "duration_ms": duration_ms,
                    },
                )
            )
            return {
                "planner_run": self.get_run(run_id),
                "source_plan": self._load_plan(previous["id"]),
                "plan": plan,
                "runtime": runtime_status,
            }
        except Exception as exc:
            self._failed += 1
            duration_ms = (perf_counter() - started) * 1000
            self._finish_run_failure(
                run_id,
                error=str(exc),
                duration_ms=duration_ms,
            )
            await self._publish_failure(
                run_id=run_id,
                workspace_id=previous.get("workspace_id"),
                run_type=PlannerRunType.REPLAN,
                exc=exc,
            )
            if isinstance(exc, ExecutionPlannerError):
                raise
            raise ExecutionPlannerError(str(exc)) from exc

    async def handle_runtime_failure(self, event: Event) -> None:
        plan_id = str(event.payload.get("plan_id") or event.correlation_id or "")
        if not plan_id:
            return

        plan = self._load_plan(plan_id)
        if plan is None:
            return

        planner_meta = dict((plan.get("metadata") or {}).get("_planner") or {})
        if planner_meta.get("auto_replan") is not True:
            return
        if int(planner_meta.get("max_replans") or 0) <= 0:
            return

        reason = str(
            event.payload.get("message")
            or event.payload.get("error")
            or "Execution Plan runtime failed."
        )

        try:
            await self.replan(
                plan_id,
                ExecutionPlanReplanRequest(
                    reason=reason,
                    preserve_completed_outputs=True,
                    auto_validate=True,
                    auto_assign=True,
                    strict_assignment=False,
                    auto_start=True,
                    wait=False,
                ),
                automatic=True,
            )
        except ExecutionPlannerError as exc:
            await self._event_bus.publish(
                Event(
                    event_type="execution_plan.planner.auto_replan_skipped",
                    source="execution_planner_service",
                    workspace_id=plan.get("workspace_id"),
                    correlation_id=plan_id,
                    payload={
                        "plan_id": plan_id,
                        "reason": str(exc),
                    },
                )
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = PlannerRunRepository(session).get(run_id)
            return None if row is None else self._serialize_run(row)

    def list_runs(
        self,
        *,
        workspace_id: str | None = None,
        source_task_id: str | None = None,
        source_plan_id: str | None = None,
        run_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = PlannerRunRepository(session).list(
                workspace_id=workspace_id,
                source_task_id=source_task_id,
                source_plan_id=source_plan_id,
                run_type=run_type,
                status=status,
                limit=limit,
                offset=offset,
            )
            return [self._serialize_run(row) for row in rows]

    async def shutdown(self) -> None:
        active = list(self._active.values())
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        self._active.clear()

    def _create_run(self, **kwargs: Any) -> str:
        with self._session_factory() as session:
            row = PlannerRunRepository(session).create(**kwargs)
            return row.id

    def _finish_run_success(
        self,
        run_id: str,
        *,
        result_plan_id: str,
        response_json: dict[str, Any],
        duration_ms: float,
    ) -> None:
        with self._session_factory() as session:
            repository = PlannerRunRepository(session)
            row = repository.get(run_id)
            if row is None:
                raise ExecutionPlannerError("Planner Run не найден.")
            repository.complete(
                row,
                result_plan_id=result_plan_id,
                response_json=response_json,
                duration_ms=duration_ms,
            )

    def _finish_run_failure(
        self,
        run_id: str,
        *,
        error: str,
        duration_ms: float,
    ) -> None:
        with self._session_factory() as session:
            repository = PlannerRunRepository(session)
            row = repository.get(run_id)
            if row is None:
                return
            repository.fail(row, error=error, duration_ms=duration_ms)

    async def _persist_plan(
        self,
        draft: ExecutionPlanCreate,
        *,
        run_id: str,
        auto_validate: bool,
        auto_assign: bool,
        strict_assignment: bool,
        root_plan_id: str | None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            repository = ExecutionPlanRepository(session)
            service = ExecutionPlanService(repository, self._event_bus)
            plan = await service.create_plan(draft)
            plan_id = str(plan["id"])

            row = repository.get(plan_id)
            assert row is not None
            metadata = deepcopy(row.metadata_json or {})
            planner_meta = dict(metadata.get("_planner") or {})
            planner_meta["root_plan_id"] = root_plan_id or plan_id
            planner_meta["run_id"] = run_id
            metadata["_planner"] = planner_meta
            row.metadata_json = metadata
            repository.session.flush()

            if auto_validate:
                validated = await service.validate_plan(plan_id)
                assert validated is not None
                if not validated["validation"].get("valid"):
                    raise ExecutionPlanError(
                        "Generated Execution Plan не прошёл validation: "
                        + "; ".join(validated["validation"].get("errors") or [])
                    )

            if auto_assign:
                try:
                    await AgentAssignmentService(
                        AgentRepository(session),
                        repository,
                        self._event_bus,
                    ).assign_plan(
                        plan_id,
                        AgentAssignmentRequest(
                            replace_existing=True,
                            strict=strict_assignment,
                        ),
                    )
                except AgentRegistryError:
                    if strict_assignment:
                        raise

            current = service.get_plan(plan_id)
            assert current is not None
            return current

    async def _start_plan(
        self,
        plan_id: str,
        *,
        wait: bool,
        wait_timeout_seconds: float,
    ) -> dict[str, Any] | None:
        if self._runtime is None:
            raise ExecutionPlannerError(
                "Execution Plan Runtime не подключён."
            )
        return await self._runtime.start(
            plan_id,
            ExecutionPlanRunRequest(
                auto_validate=True,
                auto_assign=True,
                strict_assignment=False,
                replace_assignments=False,
                wait=wait,
                wait_timeout_seconds=wait_timeout_seconds,
            ),
        )

    def _load_source_task(self, task_id: str | None) -> dict[str, Any] | None:
        if task_id is None:
            return None
        with self._session_factory() as session:
            task = session.get(TaskModel, task_id)
            if task is None:
                raise ExecutionPlannerError("Source Task не найдена.")
            return {
                "id": task.id,
                "workspace_id": task.workspace_id,
                "task_type": task.task_type,
                "priority": task.priority,
                "status": task.status,
                "title": task.title,
                "description": task.description,
                "payload": deepcopy(task.payload_json or {}),
                "executor": task.executor,
            }

    def _load_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            service = ExecutionPlanService(
                ExecutionPlanRepository(session),
                self._event_bus,
            )
            return service.get_plan(plan_id)

    def _supersede_plan(self, source_plan_id: str, replacement_plan_id: str) -> None:
        with self._session_factory() as session:
            repository = ExecutionPlanRepository(session)
            row = repository.get(source_plan_id)
            if row is None:
                return
            metadata = deepcopy(row.metadata_json or {})
            metadata["superseded_by_plan_id"] = replacement_plan_id
            row.metadata_json = metadata
            if row.status != ExecutionPlanStatus.SUPERSEDED.value:
                ExecutionPlanStateMachine.transition(
                    row,
                    ExecutionPlanStatus.SUPERSEDED,
                )

    def _count_replans(self, root_plan_id: str) -> int:
        with self._session_factory() as session:
            return PlannerRunRepository(session).count_replans_for_root(
                root_plan_id
            )

    @staticmethod
    def _build_failure_context(
        previous: dict[str, Any],
        *,
        reason: str,
        failed_step_id: str | None,
    ) -> dict[str, Any]:
        failed_steps = [
            {
                "id": step.get("id"),
                "step_key": step.get("step_key"),
                "title": step.get("title"),
                "status": step.get("status"),
                "output": deepcopy(step.get("output")),
                "runs": deepcopy(step.get("runs") or []),
            }
            for step in previous.get("steps") or []
            if step.get("status") == ExecutionStepStatus.FAILED.value
            or (failed_step_id and step.get("id") == failed_step_id)
        ]
        return {
            "reason": reason,
            "failed_step_id": failed_step_id,
            "failed_steps": failed_steps,
            "previous_plan_status": previous.get("status"),
            "previous_plan_id": previous.get("id"),
        }

    async def _publish_failure(
        self,
        *,
        run_id: str,
        workspace_id: str | None,
        run_type: PlannerRunType,
        exc: Exception,
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type="execution_plan.planner.failed",
                source="execution_planner_service",
                workspace_id=workspace_id,
                correlation_id=run_id,
                payload={
                    "run_id": run_id,
                    "run_type": run_type.value,
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                },
            )
        )

    @staticmethod
    def _serialize_run(row: ExecutionPlannerRunModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "source_task_id": row.source_task_id,
            "source_plan_id": row.source_plan_id,
            "result_plan_id": row.result_plan_id,
            "run_type": row.run_type,
            "status": row.status,
            "planner_ref": row.planner_ref,
            "request": row.request_json,
            "response": row.response_json,
            "failure_context": row.failure_context_json,
            "error": row.error,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "duration_ms": row.duration_ms,
            "created_at": row.created_at,
        }
