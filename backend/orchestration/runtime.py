from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.events import Event, EventBus
from backend.database.session import session_scope
from backend.orchestration.agent_schemas import AgentAssignmentRequest
from backend.orchestration.collaboration import (
    AgentCollaborationManager,
    CollaborationError,
)
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
    AgentProfileModel,
    ExecutionPlanModel,
    ExecutionPlanStepModel,
    ExecutionStepRunModel,
)
from backend.orchestration.repository import ExecutionPlanRepository
from backend.orchestration.runtime_schemas import ExecutionPlanRunRequest
from backend.orchestration.state_machine import ExecutionPlanStateMachine
from backend.orchestration.validator import ExecutionPlanValidator
from backend.task_engine.workflow_templates import WorkflowTemplateService


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


SessionContextFactory = Callable[[], AbstractContextManager[Session]]
ExecutionPreflightHook = Callable[[str], Awaitable[dict[str, Any]]]


class ExecutionRuntimeError(RuntimeError):
    pass


class ExecutorNotRegistered(ExecutionRuntimeError):
    pass


@dataclass(frozen=True)
class StepExecutionContext:
    plan_id: str
    plan_title: str
    objective: str
    strategy: str
    workspace_id: str | None
    step_id: str
    step_key: str
    step_type: str
    title: str
    description: str
    input: dict[str, Any]
    attempt: int
    timeout_seconds: int
    agent_id: str | None
    agent_key: str | None
    executor_ref: str | None
    model_slug: str | None
    metadata: dict[str, Any]
    conversation_id: str | None = None
    shared_context: dict[str, Any] = field(default_factory=dict)
    recent_messages: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class StepExecutionResult:
    output: dict[str, Any]
    metadata: dict[str, Any] | None = None


StepExecutor = Callable[
    [StepExecutionContext],
    Awaitable[StepExecutionResult | dict[str, Any]],
]


class ExecutorRegistry:
    def __init__(self) -> None:
        self._agent_executors: dict[str, StepExecutor] = {}
        self._tool_executors: dict[str, StepExecutor] = {}

    def register_agent(
        self,
        executor_ref: str,
        executor: StepExecutor,
        *,
        replace: bool = False,
    ) -> None:
        self._register(
            self._agent_executors,
            executor_ref,
            executor,
            replace=replace,
        )

    def register_tool(
        self,
        tool_name: str,
        executor: StepExecutor,
        *,
        replace: bool = False,
    ) -> None:
        self._register(
            self._tool_executors,
            tool_name,
            executor,
            replace=replace,
        )

    def agent(self, executor_ref: str) -> StepExecutor:
        key = self._normalize(executor_ref)
        executor = self._agent_executors.get(key)
        if executor is None:
            raise ExecutorNotRegistered(
                f"Agent executor не зарегистрирован: {executor_ref}."
            )
        return executor

    def tool(self, tool_name: str) -> StepExecutor:
        key = self._normalize(tool_name)
        executor = self._tool_executors.get(key)
        if executor is None:
            raise ExecutorNotRegistered(
                f"Tool executor не зарегистрирован: {tool_name}."
            )
        return executor

    def diagnostics(self) -> dict[str, list[str]]:
        return {
            "agent_executors": sorted(self._agent_executors),
            "tool_executors": sorted(self._tool_executors),
        }

    @classmethod
    def _register(
        cls,
        registry: dict[str, StepExecutor],
        key: str,
        executor: StepExecutor,
        *,
        replace: bool,
    ) -> None:
        normalized = cls._normalize(key)
        if normalized in registry and not replace:
            raise ValueError(f"Executor уже зарегистрирован: {normalized}.")
        registry[normalized] = executor

    @staticmethod
    def _normalize(value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("Executor key не может быть пустым.")
        return normalized


class ExecutionPlanRuntime:
    TERMINAL_STEP_STATUSES = {
        ExecutionStepStatus.COMPLETED.value,
        ExecutionStepStatus.SKIPPED.value,
        ExecutionStepStatus.FAILED.value,
        ExecutionStepStatus.CANCELLED.value,
    }
    SUCCESS_STEP_STATUSES = {
        ExecutionStepStatus.COMPLETED.value,
        ExecutionStepStatus.SKIPPED.value,
    }

    def __init__(
        self,
        *,
        event_bus: EventBus,
        session_factory: SessionContextFactory = session_scope,
        collaboration_manager: AgentCollaborationManager | None = None,
        preflight_hook: ExecutionPreflightHook | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._collaboration = collaboration_manager
        self._preflight_hook = preflight_hook
        self._registry = ExecutorRegistry()
        self._active: dict[str, asyncio.Task[None]] = {}
        self._active_delegations: dict[str, asyncio.Task[None]] = {}
        self._agent_semaphores: dict[str, asyncio.Semaphore] = {}
        self._started = 0
        self._completed = 0
        self._failed = 0
        self._cancelled = 0
        self._register_defaults()

    @property
    def registry(self) -> ExecutorRegistry:
        return self._registry

    def set_preflight_hook(
        self,
        hook: ExecutionPreflightHook | None,
    ) -> None:
        self._preflight_hook = hook

    def stats(self) -> dict[str, Any]:
        return {
            "active_plan_ids": sorted(self._active),
            "active_count": len(self._active),
            "started": self._started,
            "completed": self._completed,
            "failed": self._failed,
            "cancelled": self._cancelled,
            "preflight_enabled": self._preflight_hook is not None,
            "active_delegation_ids": sorted(self._active_delegations),
            "active_delegation_count": len(self._active_delegations),
            "collaboration": (
                self._collaboration.stats()
                if self._collaboration is not None
                else None
            ),
            **self._registry.diagnostics(),
        }

    async def start(
        self,
        plan_id: str,
        request: ExecutionPlanRunRequest,
    ) -> dict[str, Any] | None:
        current = self._active.get(plan_id)
        if current is not None and not current.done():
            raise ExecutionRuntimeError(
                "Execution Plan уже выполняется."
            )

        with self._session_factory() as session:
            if ExecutionPlanRepository(session).get(plan_id) is None:
                return None

        preflight = None
        if self._preflight_hook is not None:
            try:
                preflight = await self._preflight_hook(plan_id)
            except Exception as exc:
                raise ExecutionRuntimeError(
                    "Execution Plan отклонён preflight review: "
                    + str(exc)
                ) from exc

        await self._prepare_plan(plan_id, request)

        with self._session_factory() as session:
            plan = ExecutionPlanRepository(session).get(plan_id)
            if plan is None:
                return None
            workspace_id = plan.workspace_id

        task = asyncio.create_task(
            self._run_plan(plan_id),
            name=f"execution-plan-{plan_id}",
        )
        self._active[plan_id] = task
        task.add_done_callback(
            lambda completed, pid=plan_id: self._active.pop(pid, None)
        )
        self._started += 1

        await self._event_bus.publish(
            Event(
                event_type="execution_plan.runtime.started",
                source="execution_plan_runtime",
                workspace_id=workspace_id,
                correlation_id=plan_id,
                payload={
                    "plan_id": plan_id,
                    "preflight": preflight,
                },
            )
        )

        if request.wait:
            await self.wait(plan_id, timeout=request.wait_timeout_seconds)

        status = self.status(plan_id)
        assert status is not None
        return status

    async def wait(
        self,
        plan_id: str,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any] | None:
        task = self._active.get(plan_id)

        if task is not None:
            if timeout is None:
                await asyncio.shield(task)
            else:
                await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=timeout,
                )

        return self.status(plan_id)

    async def cancel(
        self,
        plan_id: str,
        reason: str,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            plan = ExecutionPlanRepository(session).get(plan_id)
            if plan is None:
                return None
            if plan.status in {
                ExecutionPlanStatus.COMPLETED.value,
                ExecutionPlanStatus.CANCELLED.value,
                ExecutionPlanStatus.SUPERSEDED.value,
            }:
                raise ExecutionRuntimeError(
                    f"Plan в статусе {plan.status} нельзя отменить."
                )
            metadata = dict(plan.metadata_json or {})
            metadata["runtime_cancel_reason"] = reason
            plan.metadata_json = metadata

        active = self._active.get(plan_id)
        if active is not None and not active.done():
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)
        else:
            await self._mark_plan_cancelled(plan_id, reason)

        return self.status(plan_id)

    async def run_delegation(
        self,
        delegation_id: str,
        *,
        wait: bool = True,
        wait_timeout_seconds: float | None = None,
    ) -> dict[str, Any] | None:
        if self._collaboration is None:
            raise ExecutionRuntimeError(
                "Agent Collaboration Manager не подключён."
            )

        delegation = self._collaboration.get_delegation(delegation_id)
        if delegation is None:
            return None

        current = self._active_delegations.get(delegation_id)
        if current is not None and not current.done():
            raise ExecutionRuntimeError(
                "Delegation уже выполняется."
            )

        task = asyncio.create_task(
            self._execute_delegation(delegation_id),
            name=f"agent-delegation-{delegation_id}",
        )
        self._active_delegations[delegation_id] = task
        task.add_done_callback(
            lambda completed, did=delegation_id: (
                self._active_delegations.pop(did, None)
            )
        )

        if wait:
            if wait_timeout_seconds is None:
                await asyncio.shield(task)
            else:
                await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=wait_timeout_seconds,
                )

        return self._collaboration.get_delegation(delegation_id)

    async def cancel_delegation(
        self,
        delegation_id: str,
        *,
        actor_agent_id: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any] | None:
        if self._collaboration is None:
            raise ExecutionRuntimeError(
                "Agent Collaboration Manager не подключён."
            )

        task = self._active_delegations.get(delegation_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        return await self._collaboration.cancel_delegation(
            delegation_id,
            actor_agent_id=actor_agent_id,
            note=note,
        )

    def status(self, plan_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            repository = ExecutionPlanRepository(session)
            plan = repository.get(plan_id)
            if plan is None:
                return None

            steps = repository.list_steps(plan_id)
            summary: dict[str, int] = {}
            for step in steps:
                summary[step.status] = summary.get(step.status, 0) + 1

            return {
                "plan_id": plan.id,
                "status": plan.status,
                "active": plan.id in self._active,
                "summary": summary,
                "progress": {
                    "total": len(steps),
                    "terminal": sum(
                        1
                        for step in steps
                        if step.status in self.TERMINAL_STEP_STATUSES
                    ),
                    "completed": summary.get(
                        ExecutionStepStatus.COMPLETED.value,
                        0,
                    ),
                    "skipped": summary.get(
                        ExecutionStepStatus.SKIPPED.value,
                        0,
                    ),
                    "failed": summary.get(
                        ExecutionStepStatus.FAILED.value,
                        0,
                    ),
                },
                "steps": [self._serialize_step(step) for step in steps],
                "started_at": plan.started_at,
                "finished_at": plan.finished_at,
            }

    def list_runs(
        self,
        plan_id: str,
        *,
        step_id: str | None = None,
    ) -> list[dict[str, Any]] | None:
        with self._session_factory() as session:
            repository = ExecutionPlanRepository(session)
            if repository.get(plan_id) is None:
                return None

            return [
                self._serialize_run(row)
                for row in repository.list_step_runs(
                    plan_id=plan_id,
                    step_id=step_id,
                )
            ]

    async def recover_interrupted(
        self,
        *,
        exclude_plan_ids: set[str] | None = None,
        include_plan_ids: set[str] | None = None,
    ) -> dict[str, int]:
        recovered = 0
        excluded = exclude_plan_ids or set()
        included = include_plan_ids

        with self._session_factory() as session:
            query = select(ExecutionPlanModel).where(
                ExecutionPlanModel.status
                == ExecutionPlanStatus.RUNNING.value
            )
            if excluded:
                query = query.where(
                    ExecutionPlanModel.id.not_in(tuple(excluded))
                )
            if included is not None:
                if not included:
                    plans = []
                else:
                    plans = list(
                        session.scalars(
                            query.where(
                                ExecutionPlanModel.id.in_(tuple(included))
                            )
                        ).all()
                    )
            else:
                plans = list(session.scalars(query).all())

            for plan in plans:
                for step in plan.steps:
                    if step.status in {
                        ExecutionStepStatus.RUNNING.value,
                        ExecutionStepStatus.READY.value,
                    }:
                        step.status = ExecutionStepStatus.FAILED.value
                        step.finished_at = utc_now()
                        step.output_json = {
                            "error": "Execution interrupted by process restart.",
                            "recovery": True,
                        }

                ExecutionPlanStateMachine.transition(
                    plan,
                    ExecutionPlanStatus.FAILED,
                )
                recovered += 1

        if recovered:
            await self._event_bus.publish(
                Event(
                    event_type="execution_plan.runtime.recovered",
                    source="execution_plan_runtime",
                    payload={"interrupted_failed": recovered},
                )
            )

        return {"interrupted_failed": recovered}

    async def shutdown(self) -> None:
        active = list(self._active.values())
        active_delegations = list(self._active_delegations.values())

        for task in active:
            task.cancel()
        for task in active_delegations:
            task.cancel()

        if active or active_delegations:
            await asyncio.gather(
                *active,
                *active_delegations,
                return_exceptions=True,
            )

        self._active.clear()
        self._active_delegations.clear()

    async def _prepare_plan(
        self,
        plan_id: str,
        request: ExecutionPlanRunRequest,
    ) -> None:
        with self._session_factory() as session:
            repository = ExecutionPlanRepository(session)
            plan = repository.get(plan_id)
            if plan is None:
                raise ExecutionRuntimeError("Execution Plan не найден.")

            if plan.status == ExecutionPlanStatus.DRAFT.value:
                if not request.auto_validate:
                    raise ExecutionRuntimeError(
                        "Execution Plan необходимо валидировать."
                    )
                validation = ExecutionPlanValidator.validate(
                    repository.list_steps(plan.id),
                    max_parallel_steps=plan.max_parallel_steps,
                )
                plan.validation_json = validation
                if not validation["valid"]:
                    raise ExecutionRuntimeError(
                        "Execution Plan validation failed: "
                        + "; ".join(validation["errors"])
                    )
                ExecutionPlanStateMachine.transition(
                    plan,
                    ExecutionPlanStatus.VALIDATED,
                )
                plan.validated_at = utc_now()

            if request.auto_assign:
                assignment = AgentAssignmentService(
                    AgentRepository(session),
                    repository,
                    self._event_bus,
                )
                try:
                    await assignment.assign_plan(
                        plan.id,
                        AgentAssignmentRequest(
                            replace_existing=request.replace_assignments,
                            strict=request.strict_assignment,
                        ),
                    )
                except AgentRegistryError as exc:
                    raise ExecutionRuntimeError(str(exc)) from exc

            unassigned = [
                step.step_key
                for step in repository.list_steps(plan.id)
                if step.step_type == ExecutionStepType.AGENT.value
                and not step.assigned_agent_id
            ]
            if unassigned:
                raise ExecutionRuntimeError(
                    "Agent не назначен для шагов: "
                    + ", ".join(unassigned)
                    + "."
                )

            if plan.status == ExecutionPlanStatus.VALIDATED.value:
                ExecutionPlanStateMachine.transition(
                    plan,
                    ExecutionPlanStatus.READY,
                )
            elif plan.status == ExecutionPlanStatus.FAILED.value:
                self._reset_failed_plan(plan, repository)
                ExecutionPlanStateMachine.transition(
                    plan,
                    ExecutionPlanStatus.READY,
                )
            elif plan.status != ExecutionPlanStatus.READY.value:
                raise ExecutionRuntimeError(
                    "Execution Plan нельзя запустить из статуса "
                    f"{plan.status}."
                )

            ExecutionPlanStateMachine.transition(
                plan,
                ExecutionPlanStatus.RUNNING,
            )

    async def _run_plan(self, plan_id: str) -> None:
        try:
            while True:
                wave = self._prepare_wave(plan_id)

                if wave["terminal"]:
                    if wave["status"] == ExecutionPlanStatus.COMPLETED.value:
                        self._completed += 1
                        await self._event_bus.publish(
                            Event(
                                event_type="execution_plan.runtime.completed",
                                source="execution_plan_runtime",
                                workspace_id=wave.get("workspace_id"),
                                correlation_id=plan_id,
                                payload={"plan_id": plan_id},
                            )
                        )
                    elif wave["status"] == ExecutionPlanStatus.FAILED.value:
                        self._failed += 1
                        await self._event_bus.publish(
                            Event(
                                event_type="execution_plan.runtime.failed",
                                source="execution_plan_runtime",
                                workspace_id=wave.get("workspace_id"),
                                correlation_id=plan_id,
                                payload={
                                    "plan_id": plan_id,
                                    "message": "One or more execution steps failed.",
                                },
                            )
                        )
                    return

                ready_step_ids = wave["ready_step_ids"]
                if not ready_step_ids:
                    raise ExecutionRuntimeError(
                        "Orchestration loop не нашёл готовых шагов."
                    )

                await asyncio.gather(
                    *(self._execute_step(plan_id, step_id) for step_id in ready_step_ids)
                )
        except asyncio.CancelledError:
            reason = self._cancel_reason(plan_id)
            await self._mark_plan_cancelled(plan_id, reason)
            self._cancelled += 1
        except Exception as exc:
            await self._mark_plan_failed(plan_id, str(exc))
            self._failed += 1
            await self._event_bus.publish(
                Event(
                    event_type="execution_plan.runtime.failed",
                    source="execution_plan_runtime",
                    correlation_id=plan_id,
                    payload={
                        "plan_id": plan_id,
                        "error_type": exc.__class__.__name__,
                        "message": str(exc),
                    },
                )
            )

    def _prepare_wave(self, plan_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            repository = ExecutionPlanRepository(session)
            plan = repository.get(plan_id)
            if plan is None:
                raise ExecutionRuntimeError("Execution Plan не найден.")
            if plan.status != ExecutionPlanStatus.RUNNING.value:
                return {
                    "terminal": True,
                    "status": plan.status,
                    "ready_step_ids": [],
                    "workspace_id": plan.workspace_id,
                }

            steps = repository.list_steps(plan.id)
            by_key = {step.step_key: step for step in steps}

            if any(
                step.status == ExecutionStepStatus.FAILED.value
                for step in steps
            ):
                for step in steps:
                    if step.status in {
                        ExecutionStepStatus.PENDING.value,
                        ExecutionStepStatus.READY.value,
                    }:
                        step.status = ExecutionStepStatus.CANCELLED.value
                        step.finished_at = utc_now()
                        step.output_json = {
                            "cancelled": True,
                            "reason": "upstream_step_failed",
                        }
                ExecutionPlanStateMachine.transition(
                    plan,
                    ExecutionPlanStatus.FAILED,
                )
                return {
                    "terminal": True,
                    "status": plan.status,
                    "ready_step_ids": [],
                    "workspace_id": plan.workspace_id,
                }

            for step in steps:
                if step.status != ExecutionStepStatus.PENDING.value:
                    continue

                dependencies = [
                    by_key[key]
                    for key in (step.depends_on_json or [])
                    if key in by_key
                ]

                if any(
                    item.status in {
                        ExecutionStepStatus.FAILED.value,
                        ExecutionStepStatus.CANCELLED.value,
                    }
                    for item in dependencies
                ):
                    step.status = ExecutionStepStatus.FAILED.value
                    step.finished_at = utc_now()
                    step.output_json = {
                        "error": "Dependency failed or was cancelled."
                    }
                    continue

                if all(
                    item.status in self.SUCCESS_STEP_STATUSES
                    for item in dependencies
                ):
                    context = self._build_resolution_context(plan, steps)
                    if step.condition_json and not WorkflowTemplateService.evaluate_condition(
                        dict(step.condition_json),
                        context,
                    ):
                        step.status = ExecutionStepStatus.SKIPPED.value
                        step.finished_at = utc_now()
                        step.output_json = {
                            "skipped": True,
                            "reason": "condition_false",
                            "condition": deepcopy(step.condition_json),
                        }
                    else:
                        step.status = ExecutionStepStatus.READY.value

            if all(
                step.status in self.SUCCESS_STEP_STATUSES
                for step in steps
            ):
                ExecutionPlanStateMachine.transition(
                    plan,
                    ExecutionPlanStatus.COMPLETED,
                )
                return {
                    "terminal": True,
                    "status": plan.status,
                    "ready_step_ids": [],
                    "workspace_id": plan.workspace_id,
                }

            ready = [
                step.id
                for step in steps
                if step.status == ExecutionStepStatus.READY.value
            ][: plan.max_parallel_steps]

            if not ready and all(
                step.status in self.TERMINAL_STEP_STATUSES
                for step in steps
            ):
                ExecutionPlanStateMachine.transition(
                    plan,
                    ExecutionPlanStatus.FAILED,
                )
                return {
                    "terminal": True,
                    "status": plan.status,
                    "ready_step_ids": [],
                    "workspace_id": plan.workspace_id,
                }

            return {
                "terminal": False,
                "status": plan.status,
                "ready_step_ids": ready,
                "workspace_id": plan.workspace_id,
            }

    async def _execute_step(self, plan_id: str, step_id: str) -> None:
        with self._session_factory() as session:
            repository = ExecutionPlanRepository(session)
            first_attempt = repository.next_step_attempt(step_id)

        local_attempt = 0
        max_attempts = 1

        while local_attempt < max_attempts:
            local_attempt += 1
            attempt = first_attempt + local_attempt - 1
            prepared = self._begin_step_attempt(plan_id, step_id, attempt)
            max_attempts = int(prepared["max_retries"]) + 1
            context = prepared["context"]
            run_id = str(prepared["run_id"])
            started = perf_counter()

            try:
                await self._event_bus.publish(
                    Event(
                        event_type="execution_plan.step.started",
                        source="execution_plan_runtime",
                        workspace_id=context.workspace_id,
                        correlation_id=plan_id,
                        payload={
                            "plan_id": plan_id,
                            "step_id": step_id,
                            "step_key": context.step_key,
                            "attempt": attempt,
                        },
                    )
                )

                if self._collaboration is not None and context.conversation_id:
                    await self._safe_collaboration_call(
                        "record_step_started",
                        self._collaboration.record_step_started(
                            plan_id=plan_id,
                            step_id=step_id,
                            step_key=context.step_key,
                            agent_id=context.agent_id,
                            conversation_id=context.conversation_id,
                            attempt=attempt,
                        ),
                        plan_id=plan_id,
                        step_id=step_id,
                    )

                async with asyncio.timeout(context.timeout_seconds):
                    result = await self._dispatch(context)

                if isinstance(result, StepExecutionResult):
                    output = result.output
                    result_metadata = result.metadata or {}
                elif isinstance(result, dict):
                    output = result
                    result_metadata = {}
                else:
                    raise TypeError(
                        "Step executor должен вернуть dict или StepExecutionResult."
                    )

                duration_ms = (perf_counter() - started) * 1000
                self._finish_step_success(
                    plan_id=plan_id,
                    step_id=step_id,
                    run_id=run_id,
                    output=output,
                    metadata=result_metadata,
                    duration_ms=duration_ms,
                )

                if self._collaboration is not None and context.conversation_id:
                    await self._safe_collaboration_call(
                        "record_step_completed",
                        self._collaboration.record_step_completed(
                            plan_id=plan_id,
                            step_id=step_id,
                            step_key=context.step_key,
                            agent_id=context.agent_id,
                            conversation_id=context.conversation_id,
                            output=output,
                        ),
                        plan_id=plan_id,
                        step_id=step_id,
                    )

                await self._event_bus.publish(
                    Event(
                        event_type="execution_plan.step.completed",
                        source="execution_plan_runtime",
                        workspace_id=context.workspace_id,
                        correlation_id=plan_id,
                        payload={
                            "plan_id": plan_id,
                            "step_id": step_id,
                            "step_key": context.step_key,
                            "attempt": attempt,
                            "duration_ms": duration_ms,
                        },
                    )
                )
                return
            except asyncio.CancelledError:
                duration_ms = (perf_counter() - started) * 1000
                self._finish_step_cancelled(
                    plan_id=plan_id,
                    step_id=step_id,
                    run_id=run_id,
                    duration_ms=duration_ms,
                )
                raise
            except Exception as exc:
                duration_ms = (perf_counter() - started) * 1000
                final = attempt >= max_attempts
                self._finish_step_failure(
                    plan_id=plan_id,
                    step_id=step_id,
                    run_id=run_id,
                    exc=exc,
                    duration_ms=duration_ms,
                    final=final,
                )

                if self._collaboration is not None and context.conversation_id:
                    await self._safe_collaboration_call(
                        "record_step_failed",
                        self._collaboration.record_step_failed(
                            plan_id=plan_id,
                            step_id=step_id,
                            step_key=context.step_key,
                            agent_id=context.agent_id,
                            conversation_id=context.conversation_id,
                            error=str(exc),
                            final=final,
                        ),
                        plan_id=plan_id,
                        step_id=step_id,
                    )

                await self._event_bus.publish(
                    Event(
                        event_type=(
                            "execution_plan.step.failed"
                            if final
                            else "execution_plan.step.retrying"
                        ),
                        source="execution_plan_runtime",
                        workspace_id=context.workspace_id,
                        correlation_id=plan_id,
                        payload={
                            "plan_id": plan_id,
                            "step_id": step_id,
                            "step_key": context.step_key,
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                            "error_type": exc.__class__.__name__,
                            "message": str(exc),
                        },
                    )
                )

                if final:
                    return

                await asyncio.sleep(
                    min(0.1 * (2 ** (local_attempt - 1)), 2.0)
                )

    def _begin_step_attempt(
        self,
        plan_id: str,
        step_id: str,
        attempt: int,
    ) -> dict[str, Any]:
        collaboration = {
            "conversation_id": None,
            "shared_context": {},
            "recent_messages": [],
        }

        if self._collaboration is not None:
            with self._session_factory() as preview_session:
                preview_repository = ExecutionPlanRepository(preview_session)
                preview_step = preview_repository.get_step(plan_id, step_id)
                if preview_step is None:
                    raise ExecutionRuntimeError(
                        "Execution Plan Step не найден."
                    )
                preview_agent_id = (
                    preview_step.assigned_agent_id
                    if preview_step.step_type
                    == ExecutionStepType.AGENT.value
                    else None
                )
                preview_step_key = preview_step.step_key

            # Collaboration may create a runtime conversation. It is invoked
            # outside the StepRun write transaction to avoid SQLite lock
            # contention between two independent sessions.
            collaboration = self._collaboration.prepare_step_context(
                plan_id=plan_id,
                step_key=preview_step_key,
                agent_id=preview_agent_id,
            )

        with self._session_factory() as session:
            repository = ExecutionPlanRepository(session)
            plan = repository.get(plan_id)
            step = repository.get_step(plan_id, step_id)

            if plan is None or step is None:
                raise ExecutionRuntimeError(
                    "Execution Plan или Step не найден."
                )
            if step.status not in {
                ExecutionStepStatus.READY.value,
                ExecutionStepStatus.RUNNING.value,
            }:
                raise ExecutionRuntimeError(
                    f"Step {step.step_key} нельзя запустить из статуса "
                    f"{step.status}."
                )

            all_steps = repository.list_steps(plan_id)
            resolution_context = self._build_resolution_context(
                plan,
                all_steps,
            )
            resolved_input = self._resolve_value(
                deepcopy(step.input_json or {}),
                resolution_context,
            )
            if not isinstance(resolved_input, dict):
                raise ExecutionRuntimeError(
                    "Resolved step input должен быть object."
                )

            agent = None
            executor_ref = None
            agent_key = None
            model_slug = None

            if step.step_type == ExecutionStepType.AGENT.value:
                if not step.assigned_agent_id:
                    raise ExecutionRuntimeError(
                        f"Agent не назначен для шага {step.step_key}."
                    )
                agent = session.get(
                    AgentProfileModel,
                    step.assigned_agent_id,
                )
                if agent is None or not agent.enabled:
                    raise ExecutionRuntimeError(
                        f"Назначенный Agent недоступен: "
                        f"{step.assigned_agent_id}."
                    )
                if agent.status in {"offline", "maintenance"}:
                    raise ExecutionRuntimeError(
                        f"Agent {agent.agent_key} имеет статус "
                        f"{agent.status}."
                    )
                executor_ref = agent.executor_ref
                agent_key = agent.agent_key
                model_slug = agent.model_slug

            now = utc_now()
            step.status = ExecutionStepStatus.RUNNING.value
            step.started_at = step.started_at or now
            step.finished_at = None

            run = repository.create_step_run(
                step=step,
                attempt=attempt,
                input_data=resolved_input,
                agent_id=agent.id if agent else None,
                executor_ref=executor_ref or step.tool_name,
                metadata={
                    "step_type": step.step_type,
                    "agent_key": agent_key,
                    "conversation_id": collaboration["conversation_id"],
                },
            )

            context = StepExecutionContext(
                plan_id=plan.id,
                plan_title=plan.title,
                objective=plan.objective,
                strategy=plan.strategy,
                workspace_id=plan.workspace_id,
                step_id=step.id,
                step_key=step.step_key,
                step_type=step.step_type,
                title=step.title,
                description=step.description,
                input=resolved_input,
                attempt=attempt,
                timeout_seconds=step.timeout_seconds,
                agent_id=agent.id if agent else None,
                agent_key=agent_key,
                executor_ref=executor_ref,
                model_slug=model_slug,
                metadata=dict(step.metadata_json or {}),
                conversation_id=collaboration["conversation_id"],
                shared_context=dict(collaboration["shared_context"]),
                recent_messages=tuple(collaboration["recent_messages"]),
            )

            return {
                "run_id": run.id,
                "max_retries": step.max_retries,
                "context": context,
            }

    async def _dispatch(
        self,
        context: StepExecutionContext,
    ) -> StepExecutionResult | dict[str, Any]:
        step_type = ExecutionStepType(context.step_type)

        if step_type == ExecutionStepType.AGENT:
            assert context.executor_ref is not None
            executor = self._registry.agent(context.executor_ref)
            semaphore = self._agent_semaphore(context.agent_id)
            async with semaphore:
                return await executor(context)

        if step_type == ExecutionStepType.TOOL:
            tool_name = str(context.metadata.get("tool_name") or "")
            if not tool_name:
                with self._session_factory() as session:
                    step = ExecutionPlanRepository(session).get_step(
                        context.plan_id,
                        context.step_id,
                    )
                    tool_name = step.tool_name if step else ""
            executor = self._registry.tool(tool_name)
            return await executor(context)

        if step_type == ExecutionStepType.DECISION:
            condition = context.input.get("condition")
            decision = (
                WorkflowTemplateService.evaluate_condition(
                    condition,
                    {"input": context.input, "nodes": {}},
                )
                if isinstance(condition, dict)
                else bool(context.input.get("value"))
            )
            return {
                "decision": decision,
                "selected": (
                    context.input.get("when_true")
                    if decision
                    else context.input.get("when_false")
                ),
            }

        if step_type == ExecutionStepType.CHECKPOINT:
            preserved = context.input.get("_preserved_output")
            if isinstance(preserved, dict):
                return preserved
            return {
                "checkpoint": context.step_key,
                "input": context.input,
                "recorded_at": utc_now().isoformat(),
            }

        if step_type == ExecutionStepType.TASK:
            return {
                "delegated": True,
                "task_payload": context.input,
                "executor": context.metadata.get("executor"),
            }

        if step_type == ExecutionStepType.APPROVAL:
            if context.input.get("approved") is not True:
                raise ExecutionRuntimeError(
                    "Approval step требует input.approved=true."
                )
            return {
                "approved": True,
                "approved_by": context.input.get("approved_by"),
                "note": context.input.get("note"),
            }

        raise ExecutionRuntimeError(
            f"Неподдерживаемый тип шага: {context.step_type}."
        )

    async def _execute_delegation(self, delegation_id: str) -> None:
        assert self._collaboration is not None

        started = await self._collaboration.begin_delegation(delegation_id)
        if started is None:
            return

        try:
            with self._session_factory() as session:
                plan = session.get(ExecutionPlanModel, started["plan_id"])
                agent = session.get(
                    AgentProfileModel,
                    started["delegate_agent_id"],
                )
                if plan is None:
                    raise ExecutionRuntimeError(
                        "Execution Plan для Delegation не найден."
                    )
                if agent is None or not agent.enabled:
                    raise ExecutionRuntimeError(
                        "Delegate Agent недоступен."
                    )
                if agent.status in {"offline", "maintenance"}:
                    raise ExecutionRuntimeError(
                        f"Delegate Agent имеет статус {agent.status}."
                    )

                snapshot = self._collaboration.context_snapshot(
                    plan.id,
                    agent_id=agent.id,
                    delegation_id=delegation_id,
                ) or {"values": {}}
                conversation = self._collaboration.get_conversation(
                    started["conversation_id"],
                    message_limit=25,
                )

                context = StepExecutionContext(
                    plan_id=plan.id,
                    plan_title=plan.title,
                    objective=started["objective"],
                    strategy=plan.strategy,
                    workspace_id=plan.workspace_id,
                    step_id=started["source_step_id"] or delegation_id,
                    step_key=f"delegation:{delegation_id}",
                    step_type=ExecutionStepType.AGENT.value,
                    title=started["objective"][:255],
                    description="Delegated agent execution",
                    input=dict(started["input"] or {}),
                    attempt=1,
                    timeout_seconds=int(started["timeout_seconds"]),
                    agent_id=agent.id,
                    agent_key=agent.agent_key,
                    executor_ref=agent.executor_ref,
                    model_slug=agent.model_slug,
                    metadata={
                        **dict(started["metadata"] or {}),
                        "delegation_id": delegation_id,
                        "delegation_depth": started["depth"],
                    },
                    conversation_id=started["conversation_id"],
                    shared_context=dict(snapshot["values"]),
                    recent_messages=tuple(
                        (conversation or {}).get("messages", [])
                    ),
                )

            executor = self._registry.agent(context.executor_ref or "")
            semaphore = self._agent_semaphore(context.agent_id)
            async with semaphore:
                async with asyncio.timeout(context.timeout_seconds):
                    raw_result = await executor(context)

            if isinstance(raw_result, StepExecutionResult):
                output = raw_result.output
            elif isinstance(raw_result, dict):
                output = raw_result
            else:
                raise TypeError(
                    "Delegation executor должен вернуть dict или "
                    "StepExecutionResult."
                )

            await self._collaboration.complete_delegation(
                delegation_id,
                output,
            )
        except asyncio.CancelledError:
            current = self._collaboration.get_delegation(delegation_id)
            if current is not None and current["status"] == "running":
                await self._collaboration.cancel_delegation(
                    delegation_id,
                    actor_agent_id=None,
                    note="Delegation execution cancelled.",
                )
            raise
        except Exception as exc:
            current = self._collaboration.get_delegation(delegation_id)
            if current is not None and current["status"] == "running":
                await self._collaboration.fail_delegation(
                    delegation_id,
                    str(exc),
                )

    async def _safe_collaboration_call(
        self,
        operation: str,
        awaitable: Awaitable[Any],
        *,
        plan_id: str,
        step_id: str,
    ) -> Any:
        try:
            return await awaitable
        except Exception as exc:
            await self._event_bus.publish(
                Event(
                    event_type="agent.collaboration.error",
                    source="execution_plan_runtime",
                    correlation_id=plan_id,
                    payload={
                        "plan_id": plan_id,
                        "step_id": step_id,
                        "operation": operation,
                        "error_type": exc.__class__.__name__,
                        "message": str(exc),
                    },
                )
            )
            return None

    def _agent_semaphore(
        self,
        agent_id: str | None,
    ) -> asyncio.Semaphore:
        if agent_id is None:
            return asyncio.Semaphore(1)

        semaphore = self._agent_semaphores.get(agent_id)
        if semaphore is not None:
            return semaphore

        with self._session_factory() as session:
            agent = session.get(AgentProfileModel, agent_id)
            capacity = max(agent.max_concurrency, 1) if agent else 1

        semaphore = asyncio.Semaphore(capacity)
        self._agent_semaphores[agent_id] = semaphore
        return semaphore

    def _finish_step_success(
        self,
        *,
        plan_id: str,
        step_id: str,
        run_id: str,
        output: dict[str, Any],
        metadata: dict[str, Any],
        duration_ms: float,
    ) -> None:
        with self._session_factory() as session:
            repository = ExecutionPlanRepository(session)
            step = repository.get_step(plan_id, step_id)
            run = repository.get_step_run(run_id)
            if step is None or run is None:
                raise ExecutionRuntimeError("Step или StepRun исчез во время выполнения.")

            now = utc_now()
            step.status = ExecutionStepStatus.COMPLETED.value
            step.output_json = output
            step.finished_at = now
            run.status = ExecutionStepStatus.COMPLETED.value
            run.output_json = output
            run.finished_at = now
            run.duration_ms = duration_ms
            run.metadata_json = {
                **dict(run.metadata_json or {}),
                **metadata,
            }

    def _finish_step_failure(
        self,
        *,
        plan_id: str,
        step_id: str,
        run_id: str,
        exc: Exception,
        duration_ms: float,
        final: bool,
    ) -> None:
        with self._session_factory() as session:
            repository = ExecutionPlanRepository(session)
            step = repository.get_step(plan_id, step_id)
            run = repository.get_step_run(run_id)
            if step is None or run is None:
                return

            now = utc_now()
            run.status = ExecutionStepStatus.FAILED.value
            run.error = str(exc)
            run.finished_at = now
            run.duration_ms = duration_ms

            if final:
                step.status = ExecutionStepStatus.FAILED.value
                step.finished_at = now
                step.output_json = {
                    "error": str(exc),
                    "error_type": exc.__class__.__name__,
                }
            else:
                step.status = ExecutionStepStatus.RUNNING.value

    def _finish_step_cancelled(
        self,
        *,
        plan_id: str,
        step_id: str,
        run_id: str,
        duration_ms: float,
    ) -> None:
        with self._session_factory() as session:
            repository = ExecutionPlanRepository(session)
            step = repository.get_step(plan_id, step_id)
            run = repository.get_step_run(run_id)
            if step is None or run is None:
                return

            now = utc_now()
            step.status = ExecutionStepStatus.CANCELLED.value
            step.finished_at = now
            step.output_json = {
                "cancelled": True,
                "reason": "runtime_cancelled",
            }
            run.status = ExecutionStepStatus.CANCELLED.value
            run.error = "Execution cancelled."
            run.finished_at = now
            run.duration_ms = duration_ms

    async def _mark_plan_failed(
        self,
        plan_id: str,
        reason: str,
    ) -> None:
        with self._session_factory() as session:
            repository = ExecutionPlanRepository(session)
            plan = repository.get(plan_id)
            if plan is None:
                return

            for step in repository.list_steps(plan.id):
                if step.status in {
                    ExecutionStepStatus.PENDING.value,
                    ExecutionStepStatus.READY.value,
                    ExecutionStepStatus.RUNNING.value,
                }:
                    step.status = ExecutionStepStatus.CANCELLED.value
                    step.finished_at = utc_now()
                    step.output_json = {
                        "cancelled": True,
                        "reason": "plan_failed",
                    }

            if plan.status == ExecutionPlanStatus.RUNNING.value:
                ExecutionPlanStateMachine.transition(
                    plan,
                    ExecutionPlanStatus.FAILED,
                )
            metadata = dict(plan.metadata_json or {})
            metadata["runtime_error"] = reason
            plan.metadata_json = metadata

    async def _mark_plan_cancelled(
        self,
        plan_id: str,
        reason: str,
    ) -> None:
        with self._session_factory() as session:
            repository = ExecutionPlanRepository(session)
            plan = repository.get(plan_id)
            if plan is None:
                return

            for step in repository.list_steps(plan.id):
                if step.status not in self.TERMINAL_STEP_STATUSES:
                    step.status = ExecutionStepStatus.CANCELLED.value
                    step.finished_at = utc_now()
                    step.output_json = {
                        "cancelled": True,
                        "reason": reason,
                    }

            if plan.status in {
                ExecutionPlanStatus.DRAFT.value,
                ExecutionPlanStatus.VALIDATED.value,
                ExecutionPlanStatus.READY.value,
                ExecutionPlanStatus.RUNNING.value,
                ExecutionPlanStatus.FAILED.value,
            }:
                ExecutionPlanStateMachine.transition(
                    plan,
                    ExecutionPlanStatus.CANCELLED,
                )

            metadata = dict(plan.metadata_json or {})
            metadata["runtime_cancel_reason"] = reason
            plan.metadata_json = metadata

        await self._event_bus.publish(
            Event(
                event_type="execution_plan.runtime.cancelled",
                source="execution_plan_runtime",
                correlation_id=plan_id,
                payload={"plan_id": plan_id, "reason": reason},
            )
        )

    def _cancel_reason(self, plan_id: str) -> str:
        with self._session_factory() as session:
            plan = ExecutionPlanRepository(session).get(plan_id)
            if plan is None:
                return "Execution Plan cancelled."
            return str(
                (plan.metadata_json or {}).get(
                    "runtime_cancel_reason",
                    "Execution Plan cancelled.",
                )
            )

    @staticmethod
    def _reset_failed_plan(
        plan: ExecutionPlanModel,
        repository: ExecutionPlanRepository,
    ) -> None:
        for step in repository.list_steps(plan.id):
            if step.status in {
                ExecutionStepStatus.FAILED.value,
                ExecutionStepStatus.CANCELLED.value,
                ExecutionStepStatus.READY.value,
                ExecutionStepStatus.RUNNING.value,
            }:
                step.status = ExecutionStepStatus.PENDING.value
                step.output_json = None
                step.started_at = None
                step.finished_at = None

    @staticmethod
    def _build_resolution_context(
        plan: ExecutionPlanModel,
        steps: list[ExecutionPlanStepModel],
    ) -> dict[str, Any]:
        metadata = dict(plan.metadata_json or {})
        return {
            "plan": {
                "id": plan.id,
                "title": plan.title,
                "objective": plan.objective,
                "strategy": plan.strategy,
                "metadata": metadata,
            },
            "input": dict(metadata.get("input", {})),
            "nodes": {
                step.step_key: {
                    "id": step.id,
                    "status": step.status,
                    "input": step.input_json,
                    "output": step.output_json,
                }
                for step in steps
            },
            "steps": {
                step.step_key: {
                    "id": step.id,
                    "status": step.status,
                    "input": step.input_json,
                    "output": step.output_json,
                }
                for step in steps
            },
        }

    @classmethod
    def _resolve_value(
        cls,
        value: Any,
        context: dict[str, Any],
    ) -> Any:
        if isinstance(value, dict):
            return {
                key: cls._resolve_value(item, context)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._resolve_value(item, context) for item in value]
        if not isinstance(value, str):
            return value

        if value.startswith("$") and " " not in value:
            return deepcopy(cls._read_path(context, value[1:]))

        pattern = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
        return pattern.sub(
            lambda match: str(cls._read_path(context, match.group(1))),
            value,
        )

    @staticmethod
    def _read_path(data: Any, path: str) -> Any:
        current = data
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list) and part.isdigit():
                index = int(part)
                if index >= len(current):
                    raise ExecutionRuntimeError(
                        f"Input path не найден: {path}."
                    )
                current = current[index]
            else:
                raise ExecutionRuntimeError(
                    f"Input path не найден: {path}."
                )
        return current

    def _register_defaults(self) -> None:
        for executor_ref in (
            "builtin.planner",
            "builtin.analyst",
            "builtin.writer",
            "builtin.worker",
        ):
            self._registry.register_agent(
                executor_ref,
                self._builtin_agent_executor,
            )

        self._registry.register_tool("echo", self._tool_echo)
        self._registry.register_tool("merge", self._tool_merge)
        self._registry.register_tool("select", self._tool_select)
        self._registry.register_tool("sleep", self._tool_sleep)

    @staticmethod
    async def _builtin_agent_executor(
        context: StepExecutionContext,
    ) -> dict[str, Any]:
        return {
            "agent_id": context.agent_id,
            "agent_key": context.agent_key,
            "executor_ref": context.executor_ref,
            "model_slug": context.model_slug,
            "step_key": context.step_key,
            "objective": context.objective,
            "input": context.input,
            "attempt": context.attempt,
            "conversation_id": context.conversation_id,
            "shared_context": context.shared_context,
            "recent_message_count": len(context.recent_messages),
        }

    @staticmethod
    async def _tool_echo(
        context: StepExecutionContext,
    ) -> dict[str, Any]:
        return deepcopy(context.input)

    @staticmethod
    async def _tool_merge(
        context: StepExecutionContext,
    ) -> dict[str, Any]:
        items = context.input.get("items", [])
        if not isinstance(items, list):
            raise ExecutionRuntimeError("merge.items должен быть list.")
        merged: dict[str, Any] = {}
        for item in items:
            if not isinstance(item, dict):
                raise ExecutionRuntimeError(
                    "Каждый элемент merge.items должен быть object."
                )
            merged.update(item)
        return merged

    @classmethod
    async def _tool_select(
        cls,
        context: StepExecutionContext,
    ) -> dict[str, Any]:
        path = str(context.input.get("path", ""))
        data = context.input.get("data")
        if not path:
            raise ExecutionRuntimeError("select.path обязателен.")
        return {"value": deepcopy(cls._read_path(data, path))}

    @staticmethod
    async def _tool_sleep(
        context: StepExecutionContext,
    ) -> dict[str, Any]:
        seconds = float(context.input.get("seconds", 0))
        if seconds < 0 or seconds > 3600:
            raise ExecutionRuntimeError(
                "sleep.seconds должен быть между 0 и 3600."
            )
        await asyncio.sleep(seconds)
        return {"slept_seconds": seconds}

    @staticmethod
    def _serialize_step(step: ExecutionPlanStepModel) -> dict[str, Any]:
        return {
            "id": step.id,
            "step_key": step.step_key,
            "step_type": step.step_type,
            "status": step.status,
            "assigned_agent_id": step.assigned_agent_id,
            "input": step.input_json,
            "output": step.output_json,
            "started_at": step.started_at,
            "finished_at": step.finished_at,
        }

    @staticmethod
    def _serialize_run(row: ExecutionStepRunModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "plan_id": row.plan_id,
            "step_id": row.step_id,
            "step_key": row.step_key,
            "attempt": row.attempt,
            "status": row.status,
            "agent_id": row.agent_id,
            "executor_ref": row.executor_ref,
            "input": row.input_json,
            "output": row.output_json,
            "error": row.error,
            "metadata": row.metadata_json,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "duration_ms": row.duration_ms,
            "created_at": row.created_at,
        }
