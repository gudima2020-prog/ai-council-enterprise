from __future__ import annotations

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
from backend.orchestration.critic_schemas import (
    ExecutionPlanFixRequest,
    ExecutionPlanReviewRequest,
    PlanReviewDecision,
    PlanReviewSeverity,
    PlanReviewStatus,
)
from backend.orchestration.enums import (
    ExecutionPlanStatus,
    ExecutionStepType,
)
from backend.orchestration.models import (
    ExecutionPlanReviewModel,
    ExecutionPlanStepModel,
)
from backend.orchestration.repository import ExecutionPlanRepository
from backend.orchestration.schemas import ExecutionPlanStepCreate
from backend.orchestration.service import (
    ExecutionPlanError,
    ExecutionPlanService,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


SessionContextFactory = Callable[[], AbstractContextManager[Session]]


class ExecutionPlanCriticError(RuntimeError):
    pass


class PlanReviewRejected(ExecutionPlanCriticError):
    pass


@dataclass(frozen=True)
class ReviewContext:
    review_id: str
    plan: dict[str, Any]
    request: ExecutionPlanReviewRequest
    review_round: int


class PlanCriticAdapter(Protocol):
    async def __call__(self, context: ReviewContext) -> dict[str, Any]:
        ...


class CriticAdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, PlanCriticAdapter] = {}

    def register(
        self,
        reviewer_ref: str,
        adapter: PlanCriticAdapter,
        *,
        replace: bool = False,
    ) -> None:
        key = self._normalize(reviewer_ref)
        if key in self._adapters and not replace:
            raise ValueError(f"Critic Adapter уже зарегистрирован: {key}.")
        self._adapters[key] = adapter

    def get(self, reviewer_ref: str) -> PlanCriticAdapter:
        key = self._normalize(reviewer_ref)
        adapter = self._adapters.get(key)
        if adapter is None:
            raise ExecutionPlanCriticError(
                f"Critic Adapter не зарегистрирован: {reviewer_ref}."
            )
        return adapter

    def diagnostics(self) -> list[str]:
        return sorted(self._adapters)

    @staticmethod
    def _normalize(value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("reviewer_ref не может быть пустым.")
        return normalized


class PlanReviewRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        plan_id: str,
        workspace_id: str | None,
        reviewer_ref: str,
        review_round: int,
        threshold: int,
        request_json: dict[str, Any],
    ) -> ExecutionPlanReviewModel:
        row = ExecutionPlanReviewModel(
            plan_id=plan_id,
            workspace_id=workspace_id,
            reviewer_ref=reviewer_ref,
            status=PlanReviewStatus.RUNNING.value,
            review_round=review_round,
            threshold=threshold,
            request_json=request_json,
            started_at=utc_now(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get(self, review_id: str) -> ExecutionPlanReviewModel | None:
        return self.session.get(ExecutionPlanReviewModel, review_id)

    def list(
        self,
        *,
        plan_id: str | None = None,
        workspace_id: str | None = None,
        decision: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ExecutionPlanReviewModel]:
        statement = select(ExecutionPlanReviewModel)
        if plan_id is not None:
            statement = statement.where(
                ExecutionPlanReviewModel.plan_id == plan_id
            )
        if workspace_id is not None:
            statement = statement.where(
                ExecutionPlanReviewModel.workspace_id == workspace_id
            )
        if decision is not None:
            statement = statement.where(
                ExecutionPlanReviewModel.decision == decision
            )
        statement = (
            statement
            .order_by(ExecutionPlanReviewModel.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self.session.scalars(statement).all())

    def complete(
        self,
        row: ExecutionPlanReviewModel,
        assessment: dict[str, Any],
        *,
        duration_ms: float,
    ) -> None:
        row.status = PlanReviewStatus.COMPLETED.value
        row.decision = str(assessment["decision"])
        row.score = float(assessment["score"])
        row.dimension_scores_json = deepcopy(
            assessment.get("dimension_scores") or {}
        )
        row.issues_json = deepcopy(assessment.get("issues") or [])
        row.suggested_fixes_json = deepcopy(
            assessment.get("suggested_fixes") or []
        )
        row.response_json = deepcopy(assessment)
        row.error = None
        row.finished_at = utc_now()
        row.duration_ms = duration_ms
        self.session.flush()

    def fail(
        self,
        row: ExecutionPlanReviewModel,
        *,
        error: str,
        duration_ms: float,
    ) -> None:
        row.status = PlanReviewStatus.FAILED.value
        row.error = error
        row.finished_at = utc_now()
        row.duration_ms = duration_ms
        self.session.flush()


class BuiltinPlanCritic:
    RISKY_TOOL_MARKERS = {
        "delete",
        "remove",
        "send",
        "email",
        "payment",
        "transfer",
        "trade",
        "order",
        "execute",
        "subprocess",
        "shell",
        "http",
        "publish",
        "deploy",
    }

    async def __call__(self, context: ReviewContext) -> dict[str, Any]:
        plan = context.plan
        steps = list(plan.get("steps") or [])
        issues: list[dict[str, Any]] = []
        fixes: list[dict[str, Any]] = []
        keys = {str(step.get("step_key")) for step in steps}

        def issue(
            code: str,
            severity: PlanReviewSeverity,
            message: str,
            *,
            step_key: str | None = None,
            fixable: bool = False,
            fix_hint: str | None = None,
        ) -> None:
            payload = {
                "code": code,
                "severity": severity.value,
                "message": message,
                "step_key": step_key,
                "fixable": fixable,
                "fix_hint": fix_hint,
            }
            issues.append(payload)
            if fixable and not any(item["code"] == code for item in fixes):
                fixes.append(
                    {
                        "code": code,
                        "description": fix_hint or message,
                    }
                )

        structural = 100.0
        coverage = 100.0
        safety = 100.0
        executability = 100.0
        resilience = 100.0
        observability = 100.0

        if not steps:
            issue(
                "empty_plan",
                PlanReviewSeverity.CRITICAL,
                "Execution Plan не содержит шагов.",
            )
            structural = 0
            coverage = 0
            executability = 0

        if not str(plan.get("objective") or "").strip():
            issue(
                "missing_objective",
                PlanReviewSeverity.CRITICAL,
                "У плана отсутствует цель.",
            )
            coverage -= 60

        unknown_found = False
        self_found = False
        graph: dict[str, set[str]] = {}
        dependents: dict[str, set[str]] = {key: set() for key in keys}

        for step in steps:
            key = str(step.get("step_key"))
            dependencies = [str(item) for item in step.get("depends_on") or []]
            graph[key] = set(dependencies)

            if key in dependencies:
                self_found = True
                issue(
                    "self_dependency",
                    PlanReviewSeverity.HIGH,
                    "Шаг зависит от самого себя.",
                    step_key=key,
                    fixable=True,
                    fix_hint="Удалить self-dependency.",
                )
            unknown = sorted(set(dependencies) - keys)
            if unknown:
                unknown_found = True
                issue(
                    "unknown_dependency",
                    PlanReviewSeverity.HIGH,
                    "Шаг содержит неизвестные зависимости: "
                    + ", ".join(unknown),
                    step_key=key,
                    fixable=True,
                    fix_hint="Удалить ссылки на отсутствующие шаги.",
                )
            for dependency in set(dependencies) & keys:
                dependents.setdefault(dependency, set()).add(key)

        if unknown_found:
            structural -= 30
        if self_found:
            structural -= 25

        if self._has_cycle(graph, keys):
            issue(
                "dependency_cycle",
                PlanReviewSeverity.CRITICAL,
                "Граф Execution Plan содержит цикл.",
            )
            structural = 0

        terminal = sorted(key for key in keys if not dependents.get(key))
        if len(terminal) == 0 and steps:
            issue(
                "no_terminal_step",
                PlanReviewSeverity.HIGH,
                "У плана отсутствует конечный шаг.",
            )
            coverage -= 35
        elif len(terminal) > 3:
            issue(
                "too_many_terminal_steps",
                PlanReviewSeverity.MEDIUM,
                "План имеет слишком много независимых конечных результатов.",
            )
            coverage -= 15

        risky_steps: list[dict[str, Any]] = []
        approval_keys = {
            str(step.get("step_key"))
            for step in steps
            if str(step.get("step_type")) == ExecutionStepType.APPROVAL.value
        }

        for step in steps:
            key = str(step.get("step_key"))
            step_type = str(step.get("step_type"))
            role = str(step.get("agent_role") or "").strip()
            capability = str(step.get("capability") or "").strip()
            tool_name = str(step.get("tool_name") or "").strip().lower()
            metadata = dict(step.get("metadata") or {})

            if step_type == ExecutionStepType.AGENT.value:
                if not role:
                    issue(
                        "missing_agent_role",
                        PlanReviewSeverity.HIGH,
                        "Agent step не содержит agent_role.",
                        step_key=key,
                    )
                    executability -= 18
                if not capability:
                    issue(
                        "missing_capability",
                        PlanReviewSeverity.MEDIUM,
                        "Agent step не содержит capability.",
                        step_key=key,
                        fixable=True,
                        fix_hint="Определить capability по роли агента.",
                    )
                    executability -= 10
                if not step.get("assigned_agent_id"):
                    issue(
                        "agent_not_assigned",
                        PlanReviewSeverity.MEDIUM,
                        "Для Agent step ещё не назначен исполнитель.",
                        step_key=key,
                    )
                    executability -= 7

            if step_type == ExecutionStepType.TOOL.value:
                if not tool_name:
                    issue(
                        "missing_tool_name",
                        PlanReviewSeverity.HIGH,
                        "Tool step не содержит tool_name.",
                        step_key=key,
                    )
                    executability -= 20
                risk_level = str(metadata.get("risk_level") or "").lower()
                risky = risk_level in {"high", "critical"} or any(
                    marker in tool_name
                    for marker in self.RISKY_TOOL_MARKERS
                )
                if risky:
                    risky_steps.append(step)
                    dependencies = set(step.get("depends_on") or [])
                    if not (dependencies & approval_keys):
                        issue(
                            "risky_tool_without_approval",
                            PlanReviewSeverity.HIGH,
                            "Рискованный Tool step не защищён Approval Gate.",
                            step_key=key,
                            fixable=True,
                            fix_hint="Добавить Approval step перед инструментом.",
                        )
                        safety -= 30

            timeout_seconds = int(step.get("timeout_seconds") or 0)
            if timeout_seconds < 30:
                issue(
                    "timeout_too_low",
                    PlanReviewSeverity.MEDIUM,
                    "Timeout шага ниже безопасного минимума 30 секунд.",
                    step_key=key,
                    fixable=True,
                    fix_hint="Увеличить timeout до 30 секунд.",
                )
                resilience -= 8

            if (
                step_type == ExecutionStepType.AGENT.value
                and int(step.get("max_retries") or 0) == 0
            ):
                issue(
                    "agent_without_retry",
                    PlanReviewSeverity.LOW,
                    "Agent step не имеет повторной попытки.",
                    step_key=key,
                    fixable=True,
                    fix_hint="Добавить одну повторную попытку.",
                )
                resilience -= 4

        has_quality_review = any(
            self._is_quality_review(step)
            for step in steps
        )
        if len(steps) >= 3 and not has_quality_review:
            issue(
                "missing_quality_review",
                PlanReviewSeverity.MEDIUM,
                "В плане отсутствует независимая проверка результата.",
                fixable=True,
                fix_hint="Добавить финальный quality review step.",
            )
            observability -= 25

        if not str(plan.get("strategy") or "").strip():
            issue(
                "missing_strategy",
                PlanReviewSeverity.LOW,
                "Стратегия выполнения не описана.",
                fixable=True,
                fix_hint="Добавить базовое описание стратегии.",
            )
            coverage -= 5

        dimensions = {
            "structural_integrity": max(structural, 0),
            "objective_coverage": max(coverage, 0),
            "safety": max(safety, 0),
            "executability": max(executability, 0),
            "resilience": max(resilience, 0),
            "observability": max(observability, 0),
        }
        weights = {
            "structural_integrity": 0.25,
            "objective_coverage": 0.20,
            "safety": 0.20,
            "executability": 0.15,
            "resilience": 0.10,
            "observability": 0.10,
        }
        score = round(
            sum(dimensions[key] * weights[key] for key in dimensions),
            2,
        )
        severities = {item["severity"] for item in issues}

        if PlanReviewSeverity.CRITICAL.value in severities:
            decision = PlanReviewDecision.REJECT
        elif (
            score >= context.request.threshold
            and PlanReviewSeverity.HIGH.value not in severities
        ):
            decision = PlanReviewDecision.PASS
        else:
            decision = PlanReviewDecision.REVISE

        return {
            "plan_id": plan["id"],
            "review_id": context.review_id,
            "review_round": context.review_round,
            "reviewer_ref": context.request.reviewer_ref,
            "threshold": context.request.threshold,
            "score": score,
            "decision": decision.value,
            "dimension_scores": dimensions,
            "issues": issues,
            "suggested_fixes": fixes,
            "summary": {
                "step_count": len(steps),
                "terminal_steps": terminal,
                "risky_step_count": len(risky_steps),
                "issue_count": len(issues),
            },
        }

    @staticmethod
    def _has_cycle(
        graph: dict[str, set[str]],
        keys: set[str],
    ) -> bool:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> bool:
            if node in visiting:
                return True
            if node in visited:
                return False

            visiting.add(node)
            for dependency in graph.get(node, set()) & keys:
                if visit(dependency):
                    return True
            visiting.remove(node)
            visited.add(node)
            return False

        return any(visit(node) for node in keys if node not in visited)

    @staticmethod
    def _is_quality_review(step: dict[str, Any]) -> bool:
        text = " ".join(
            [
                str(step.get("step_key") or ""),
                str(step.get("title") or ""),
                str(step.get("capability") or ""),
            ]
        ).lower()
        return any(
            marker in text
            for marker in (
                "review",
                "verify",
                "critic",
                "quality",
                "провер",
                "контрол",
            )
        )


class ExecutionPlanCriticService:
    def __init__(
        self,
        *,
        event_bus: EventBus,
        session_factory: SessionContextFactory = session_scope,
    ) -> None:
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._registry = CriticAdapterRegistry()
        self._registry.register("builtin.critic", BuiltinPlanCritic())
        self._reviewed = 0
        self._passed = 0
        self._revised = 0
        self._rejected = 0
        self._auto_fixed = 0
        self._failed = 0

    @property
    def registry(self) -> CriticAdapterRegistry:
        return self._registry

    def stats(self) -> dict[str, Any]:
        return {
            "reviewed": self._reviewed,
            "passed": self._passed,
            "revised": self._revised,
            "rejected": self._rejected,
            "auto_fixed": self._auto_fixed,
            "failed": self._failed,
            "reviewer_adapters": self._registry.diagnostics(),
        }

    async def review(
        self,
        plan_id: str,
        request: ExecutionPlanReviewRequest,
        *,
        review_round: int = 1,
    ) -> dict[str, Any] | None:
        plan = self._load_plan(plan_id)
        if plan is None:
            return None
        if plan["status"] in {
            ExecutionPlanStatus.RUNNING.value,
            ExecutionPlanStatus.COMPLETED.value,
            ExecutionPlanStatus.CANCELLED.value,
            ExecutionPlanStatus.SUPERSEDED.value,
        }:
            raise ExecutionPlanCriticError(
                "Нельзя проверять выполняющийся или завершённый Plan."
            )

        review_id = self._create_review(
            plan=plan,
            request=request,
            review_round=review_round,
        )
        started = perf_counter()

        try:
            adapter = self._registry.get(request.reviewer_ref)
            assessment = await adapter(
                ReviewContext(
                    review_id=review_id,
                    plan=plan,
                    request=request,
                    review_round=review_round,
                )
            )
            duration_ms = (perf_counter() - started) * 1000
            self._finish_success(
                review_id,
                assessment=assessment,
                duration_ms=duration_ms,
            )
            self._store_plan_summary(plan_id, assessment)
            self._reviewed += 1

            decision = assessment["decision"]
            if decision == PlanReviewDecision.PASS.value:
                self._passed += 1
            elif decision == PlanReviewDecision.REVISE.value:
                self._revised += 1
            else:
                self._rejected += 1

            await self._event_bus.publish(
                Event(
                    event_type=f"execution_plan.review.{decision}",
                    source="execution_plan_critic_service",
                    workspace_id=plan.get("workspace_id"),
                    correlation_id=plan_id,
                    payload={
                        "plan_id": plan_id,
                        "review_id": review_id,
                        "review_round": review_round,
                        "score": assessment["score"],
                        "threshold": request.threshold,
                        "decision": decision,
                        "issue_count": len(assessment.get("issues") or []),
                    },
                )
            )
            return self.get_review(review_id)
        except Exception as exc:
            self._failed += 1
            duration_ms = (perf_counter() - started) * 1000
            self._finish_failure(
                review_id,
                error=str(exc),
                duration_ms=duration_ms,
            )
            await self._event_bus.publish(
                Event(
                    event_type="execution_plan.review.failed",
                    source="execution_plan_critic_service",
                    workspace_id=plan.get("workspace_id"),
                    correlation_id=plan_id,
                    payload={
                        "plan_id": plan_id,
                        "review_id": review_id,
                        "message": str(exc),
                    },
                )
            )
            if isinstance(exc, ExecutionPlanCriticError):
                raise
            raise ExecutionPlanCriticError(str(exc)) from exc

    async def review_and_fix(
        self,
        plan_id: str,
        request: ExecutionPlanReviewRequest,
    ) -> dict[str, Any] | None:
        history: list[dict[str, Any]] = []
        applied: list[dict[str, Any]] = []

        for review_round in range(1, request.max_rounds + 1):
            review = await self.review(
                plan_id,
                request,
                review_round=review_round,
            )
            if review is None:
                return None
            history.append(review)

            if review.get("decision") == PlanReviewDecision.PASS.value:
                return {
                    "plan_id": plan_id,
                    "decision": PlanReviewDecision.PASS.value,
                    "score": review.get("score"),
                    "threshold": request.threshold,
                    "rounds": len(history),
                    "reviews": history,
                    "applied_fixes": applied,
                }

            if not request.auto_fix or review_round >= request.max_rounds:
                break

            suggested = list(review.get("suggested_fixes") or [])
            fix_codes = [str(item["code"]) for item in suggested]
            if not fix_codes:
                break

            fix_result = await self.apply_fixes(
                plan_id,
                ExecutionPlanFixRequest(
                    review_id=review["id"],
                    fix_codes=fix_codes,
                    auto_assign_after_fix=request.auto_assign_after_fix,
                    strict_assignment=request.strict_assignment,
                ),
            )
            applied.extend(fix_result["applied_fixes"])
            self._auto_fixed += len(fix_result["applied_fixes"])

        latest = history[-1]
        result = {
            "plan_id": plan_id,
            "decision": latest.get("decision"),
            "score": latest.get("score"),
            "threshold": request.threshold,
            "rounds": len(history),
            "reviews": history,
            "applied_fixes": applied,
        }

        if request.require_pass:
            raise PlanReviewRejected(
                "Execution Plan не прошёл Critic Review: "
                f"score={latest.get('score')}, "
                f"threshold={request.threshold}, "
                f"decision={latest.get('decision')}."
            )
        return result

    async def ensure_approved_for_run(
        self,
        plan_id: str,
    ) -> dict[str, Any]:
        plan = self._load_plan(plan_id)
        if plan is None:
            raise ExecutionPlanCriticError("Execution Plan не найден.")

        policy = dict((plan.get("metadata") or {}).get("_review_policy") or {})
        if policy.get("enabled", True) is False:
            return {
                "plan_id": plan_id,
                "skipped": True,
                "reason": "review_disabled",
            }

        result = await self.review_and_fix(
            plan_id,
            ExecutionPlanReviewRequest(
                reviewer_ref=str(
                    policy.get("reviewer_ref") or "builtin.critic"
                ),
                threshold=int(policy.get("threshold") or 80),
                auto_fix=bool(policy.get("auto_fix", True)),
                max_rounds=int(policy.get("max_rounds") or 2),
                require_pass=bool(policy.get("require_pass", True)),
                auto_assign_after_fix=True,
                strict_assignment=False,
                context={"source": "execution_runtime_preflight"},
            ),
        )
        assert result is not None
        return result

    async def apply_fixes(
        self,
        plan_id: str,
        request: ExecutionPlanFixRequest,
    ) -> dict[str, Any]:
        requested_codes = set(request.fix_codes)
        if request.review_id:
            review = self.get_review(request.review_id)
            if review is None:
                raise ExecutionPlanCriticError("Plan Review не найден.")
            if review["plan_id"] != plan_id:
                raise ExecutionPlanCriticError(
                    "Review принадлежит другому Execution Plan."
                )
            if not requested_codes:
                requested_codes = {
                    str(item["code"])
                    for item in review.get("suggested_fixes") or []
                }

        applied: list[dict[str, Any]] = []

        with self._session_factory() as session:
            repository = ExecutionPlanRepository(session)
            plan = repository.get(plan_id)
            if plan is None:
                raise ExecutionPlanCriticError("Execution Plan не найден.")
            if plan.status in {
                ExecutionPlanStatus.RUNNING.value,
                ExecutionPlanStatus.COMPLETED.value,
                ExecutionPlanStatus.CANCELLED.value,
                ExecutionPlanStatus.SUPERSEDED.value,
            }:
                raise ExecutionPlanCriticError(
                    "Plan в текущем статусе нельзя автоматически изменять."
                )

            steps = repository.list_steps(plan_id)
            keys = {step.step_key for step in steps}

            if requested_codes & {"self_dependency", "unknown_dependency"}:
                for step in steps:
                    before = list(step.depends_on_json or [])
                    after = list(
                        dict.fromkeys(
                            item
                            for item in before
                            if item in keys and item != step.step_key
                        )
                    )
                    if after != before:
                        step.depends_on_json = after
                        applied.append(
                            {
                                "code": "normalize_dependencies",
                                "step_key": step.step_key,
                                "before": before,
                                "after": after,
                            }
                        )

            if "missing_capability" in requested_codes:
                capability_by_role = {
                    "analyst": "analysis",
                    "planner": "execution_plan_design",
                    "writer": "report_generation",
                    "worker": "task_execution",
                    "reviewer": "quality_review",
                }
                for step in steps:
                    if (
                        step.step_type == ExecutionStepType.AGENT.value
                        and not (step.capability or "").strip()
                    ):
                        step.capability = capability_by_role.get(
                            str(step.agent_role or "").strip().lower(),
                            "task_execution",
                        )
                        applied.append(
                            {
                                "code": "infer_capability",
                                "step_key": step.step_key,
                                "value": step.capability,
                            }
                        )

            if "timeout_too_low" in requested_codes:
                for step in steps:
                    if int(step.timeout_seconds or 0) < 30:
                        before = step.timeout_seconds
                        step.timeout_seconds = 30
                        applied.append(
                            {
                                "code": "raise_timeout",
                                "step_key": step.step_key,
                                "before": before,
                                "after": 30,
                            }
                        )

            if "agent_without_retry" in requested_codes:
                for step in steps:
                    if (
                        step.step_type == ExecutionStepType.AGENT.value
                        and int(step.max_retries or 0) == 0
                    ):
                        step.max_retries = 1
                        applied.append(
                            {
                                "code": "add_agent_retry",
                                "step_key": step.step_key,
                                "after": 1,
                            }
                        )

            if "missing_strategy" in requested_codes and not plan.strategy.strip():
                plan.strategy = (
                    "Выполнить шаги в соответствии с DAG, проверить риски, "
                    "сохранить промежуточные результаты и провести итоговый review."
                )
                applied.append({"code": "add_strategy"})

            if "risky_tool_without_approval" in requested_codes:
                risky_steps = [
                    step
                    for step in steps
                    if step.step_type == ExecutionStepType.TOOL.value
                    and self._is_risky_tool(step)
                ]
                for step in risky_steps:
                    dependencies = list(step.depends_on_json or [])
                    approval_key = self._unique_key(
                        f"approve_{step.step_key}",
                        keys,
                    )
                    approval = repository.add_step(
                        plan_id,
                        ExecutionPlanStepCreate(
                            step_key=approval_key,
                            sequence=max(int(step.sequence or 0) - 1, 0),
                            step_type=ExecutionStepType.APPROVAL,
                            title=f"Подтвердить: {step.title}",
                            description=(
                                "Approval Gate добавлен Critic Service перед "
                                "выполнением рискованного инструмента."
                            ),
                            input={
                                "target_step_key": step.step_key,
                                "tool_name": step.tool_name,
                                "risk": "high",
                            },
                            depends_on=dependencies,
                            timeout_seconds=86_400,
                            max_retries=0,
                            metadata={
                                "generated_by": "builtin.critic",
                                "review_gate": True,
                            },
                        ),
                    )
                    keys.add(approval.step_key)
                    step.depends_on_json = [approval.step_key]
                    applied.append(
                        {
                            "code": "add_approval_gate",
                            "step_key": step.step_key,
                            "approval_step_key": approval.step_key,
                        }
                    )

            if "missing_quality_review" in requested_codes:
                current_steps = repository.list_steps(plan_id)
                if not any(self._is_review_step(step) for step in current_steps):
                    dependents = {
                        dependency
                        for step in current_steps
                        for dependency in (step.depends_on_json or [])
                    }
                    terminal = [
                        step
                        for step in current_steps
                        if step.step_key not in dependents
                    ]
                    final_step = max(
                        terminal or current_steps,
                        key=lambda item: (item.sequence, item.step_key),
                    )
                    review_key = self._unique_key("quality_review", keys)
                    old_dependencies = list(final_step.depends_on_json or [])
                    review_dependencies = (
                        old_dependencies
                        if old_dependencies
                        else [
                            step.step_key
                            for step in current_steps
                            if step.id != final_step.id
                            and step.sequence <= final_step.sequence
                        ][-3:]
                    )
                    review_step = repository.add_step(
                        plan_id,
                        ExecutionPlanStepCreate(
                            step_key=review_key,
                            sequence=max(int(final_step.sequence or 0) - 1, 0),
                            step_type=ExecutionStepType.AGENT,
                            title="Провести независимую проверку качества",
                            description=(
                                "Проверить полноту, фактическую корректность, "
                                "риски и соответствие результата цели."
                            ),
                            agent_role="analyst",
                            capability="quality_review",
                            input={
                                "objective": plan.objective,
                                "candidate_outputs": {
                                    key: f"$steps.{key}.output"
                                    for key in review_dependencies
                                },
                            },
                            depends_on=review_dependencies,
                            timeout_seconds=300,
                            max_retries=1,
                            metadata={
                                "generated_by": "builtin.critic",
                                "quality_gate": True,
                            },
                        ),
                    )
                    final_step.depends_on_json = [review_step.step_key]
                    keys.add(review_step.step_key)
                    applied.append(
                        {
                            "code": "add_quality_review",
                            "review_step_key": review_step.step_key,
                            "before_step_key": final_step.step_key,
                        }
                    )

            plan.status = ExecutionPlanStatus.DRAFT.value
            plan.validation_json = {}
            plan.validated_at = None
            plan.finished_at = None
            session.flush()

            service = ExecutionPlanService(repository, self._event_bus)
            validated = await service.validate_plan(plan_id)
            if validated is None:
                raise ExecutionPlanCriticError("Execution Plan не найден.")
            if not validated["validation"].get("valid"):
                raise ExecutionPlanCriticError(
                    "Auto-fix создал невалидный Plan: "
                    + "; ".join(
                        validated["validation"].get("errors") or []
                    )
                )

            if request.auto_assign_after_fix:
                try:
                    await AgentAssignmentService(
                        AgentRepository(session),
                        repository,
                        self._event_bus,
                    ).assign_plan(
                        plan_id,
                        AgentAssignmentRequest(
                            replace_existing=False,
                            strict=request.strict_assignment,
                        ),
                    )
                except AgentRegistryError:
                    if request.strict_assignment:
                        raise

            if request.review_id:
                review_row = PlanReviewRepository(session).get(
                    request.review_id
                )
                if review_row is not None:
                    review_row.applied_fixes_json = deepcopy(applied)

        await self._event_bus.publish(
            Event(
                event_type="execution_plan.review.fixes_applied",
                source="execution_plan_critic_service",
                correlation_id=plan_id,
                payload={
                    "plan_id": plan_id,
                    "review_id": request.review_id,
                    "applied_fixes": applied,
                },
            )
        )
        return {
            "plan_id": plan_id,
            "review_id": request.review_id,
            "applied_fixes": applied,
            "plan": self._load_plan(plan_id),
        }

    def get_review(self, review_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = PlanReviewRepository(session).get(review_id)
            return None if row is None else self._serialize_review(row)

    def list_reviews(
        self,
        *,
        plan_id: str | None = None,
        workspace_id: str | None = None,
        decision: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = PlanReviewRepository(session).list(
                plan_id=plan_id,
                workspace_id=workspace_id,
                decision=decision,
                limit=limit,
                offset=offset,
            )
            return [self._serialize_review(row) for row in rows]

    def _create_review(
        self,
        *,
        plan: dict[str, Any],
        request: ExecutionPlanReviewRequest,
        review_round: int,
    ) -> str:
        with self._session_factory() as session:
            row = PlanReviewRepository(session).create(
                plan_id=plan["id"],
                workspace_id=plan.get("workspace_id"),
                reviewer_ref=request.reviewer_ref,
                review_round=review_round,
                threshold=request.threshold,
                request_json=request.model_dump(mode="json"),
            )
            return row.id

    def _finish_success(
        self,
        review_id: str,
        *,
        assessment: dict[str, Any],
        duration_ms: float,
    ) -> None:
        with self._session_factory() as session:
            repository = PlanReviewRepository(session)
            row = repository.get(review_id)
            if row is None:
                raise ExecutionPlanCriticError("Plan Review не найден.")
            repository.complete(
                row,
                assessment,
                duration_ms=duration_ms,
            )

    def _finish_failure(
        self,
        review_id: str,
        *,
        error: str,
        duration_ms: float,
    ) -> None:
        with self._session_factory() as session:
            repository = PlanReviewRepository(session)
            row = repository.get(review_id)
            if row is not None:
                repository.fail(
                    row,
                    error=error,
                    duration_ms=duration_ms,
                )

    def _load_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            return ExecutionPlanService(
                ExecutionPlanRepository(session),
                self._event_bus,
            ).get_plan(plan_id)

    def _store_plan_summary(
        self,
        plan_id: str,
        assessment: dict[str, Any],
    ) -> None:
        with self._session_factory() as session:
            repository = ExecutionPlanRepository(session)
            plan = repository.get(plan_id)
            if plan is None:
                return
            metadata = deepcopy(plan.metadata_json or {})
            metadata["_review_summary"] = {
                "review_id": assessment["review_id"],
                "review_round": assessment["review_round"],
                "reviewer_ref": assessment["reviewer_ref"],
                "score": assessment["score"],
                "threshold": assessment["threshold"],
                "decision": assessment["decision"],
                "issue_count": len(assessment.get("issues") or []),
                "reviewed_at": utc_now().isoformat(),
            }
            plan.metadata_json = metadata
            session.flush()

    @staticmethod
    def _is_review_step(step: ExecutionPlanStepModel) -> bool:
        text = " ".join(
            [
                step.step_key,
                step.title,
                step.capability or "",
            ]
        ).lower()
        return any(
            marker in text
            for marker in (
                "review",
                "verify",
                "critic",
                "quality",
                "провер",
                "контрол",
            )
        )

    @staticmethod
    def _is_risky_tool(step: ExecutionPlanStepModel) -> bool:
        metadata = dict(step.metadata_json or {})
        if str(metadata.get("risk_level") or "").lower() in {
            "high",
            "critical",
        }:
            return True
        tool_name = str(step.tool_name or "").lower()
        return any(
            marker in tool_name
            for marker in BuiltinPlanCritic.RISKY_TOOL_MARKERS
        )

    @staticmethod
    def _unique_key(base: str, existing: set[str]) -> str:
        candidate = base[:64]
        index = 2
        while candidate in existing:
            suffix = f"_{index}"
            candidate = base[: 64 - len(suffix)] + suffix
            index += 1
        return candidate

    @staticmethod
    def _serialize_review(row: ExecutionPlanReviewModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "plan_id": row.plan_id,
            "workspace_id": row.workspace_id,
            "reviewer_ref": row.reviewer_ref,
            "status": row.status,
            "decision": row.decision,
            "review_round": row.review_round,
            "threshold": row.threshold,
            "score": row.score,
            "dimension_scores": row.dimension_scores_json,
            "issues": row.issues_json,
            "suggested_fixes": row.suggested_fixes_json,
            "applied_fixes": row.applied_fixes_json,
            "request": row.request_json,
            "response": row.response_json,
            "error": row.error,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "duration_ms": row.duration_ms,
            "created_at": row.created_at,
        }
