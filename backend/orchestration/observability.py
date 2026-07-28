from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.events import Event, EventBus
from backend.database.session import session_scope
from backend.orchestration.models import (
    AgentProfileModel,
    ExecutionMetricSnapshotModel,
    ExecutionPlanModel,
    ExecutionPlanReviewModel,
    ExecutionPlannerRunModel,
    ExecutionSLOBreachModel,
    ExecutionSLOPolicyModel,
    ExecutionStepRunModel,
    ExecutionSupervisorIncidentModel,
    ToolInvocationModel,
)
from backend.orchestration.observability_schemas import (
    ExecutionSLOPolicyCreate,
    ExecutionSLOPolicyUpdate,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    normalized = ensure_utc(value)
    return None if normalized is None else normalized.isoformat()


def percentage(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100.0, 4)


def percentile(values: list[float], percentile_value: float) -> float | None:
    cleaned = sorted(float(value) for value in values if value is not None)
    if not cleaned:
        return None
    if len(cleaned) == 1:
        return round(cleaned[0], 3)
    rank = max(1, ceil((percentile_value / 100.0) * len(cleaned)))
    return round(cleaned[min(rank - 1, len(cleaned) - 1)], 3)


SessionContextFactory = Callable[[], AbstractContextManager[Session]]


class ExecutionObservabilityError(RuntimeError):
    pass


class ExecutionSLOPolicyNotFound(ExecutionObservabilityError):
    pass


class ExecutionSLOBreachNotFound(ExecutionObservabilityError):
    pass


class ExecutionObservabilityService:
    """Collects orchestration metrics and evaluates SLO policies.

    Metrics are calculated from persistent orchestration tables. This keeps the
    dashboard valid after an application restart and makes SLO evidence
    reproducible for a selected time window.
    """

    def __init__(
        self,
        *,
        event_bus: EventBus,
        session_factory: SessionContextFactory = session_scope,
    ) -> None:
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._running = False
        self._monitor_task: asyncio.Task[None] | None = None
        self._events_seen = 0
        self._collections = 0
        self._evaluations = 0
        self._evaluation_failures = 0
        self._last_collection_at: datetime | None = None
        self._last_evaluation_at: datetime | None = None
        self._dirty = True

    async def start(self) -> None:
        if self._running:
            return
        self._ensure_default_policy()
        self._running = True
        self._monitor_task = asyncio.create_task(
            self._monitor_loop(),
            name="execution-observability-monitor",
        )

    async def shutdown(self) -> None:
        self._running = False
        monitor = self._monitor_task
        self._monitor_task = None
        if monitor is not None:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)

    async def handle_event(self, event: Event) -> None:
        self._events_seen += 1
        self._dirty = True

    def stats(self) -> dict[str, Any]:
        with self._session_factory() as session:
            policies = list(session.scalars(select(ExecutionSLOPolicyModel)).all())
            snapshots = list(
                session.scalars(select(ExecutionMetricSnapshotModel)).all()
            )
            breaches = list(session.scalars(select(ExecutionSLOBreachModel)).all())

        return {
            "running": self._running,
            "events_seen": self._events_seen,
            "collections": self._collections,
            "evaluations": self._evaluations,
            "evaluation_failures": self._evaluation_failures,
            "last_collection_at": iso(self._last_collection_at),
            "last_evaluation_at": iso(self._last_evaluation_at),
            "policies": len(policies),
            "enabled_policies": sum(1 for row in policies if row.enabled),
            "snapshots": len(snapshots),
            "open_breaches": sum(1 for row in breaches if row.status == "open"),
            "capabilities": [
                "persistent_metrics",
                "operational_dashboard",
                "windowed_percentiles",
                "workspace_slo_policies",
                "automatic_breach_detection",
                "automatic_recovery_detection",
                "prometheus_export",
            ],
        }

    def create_policy(
        self,
        request: ExecutionSLOPolicyCreate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            existing = session.scalar(
                select(ExecutionSLOPolicyModel).where(
                    ExecutionSLOPolicyModel.scope_key == request.scope_key
                )
            )
            if existing is not None:
                raise ExecutionObservabilityError(
                    f"SLO policy already exists for scope {request.scope_key}."
                )
            row = ExecutionSLOPolicyModel(
                scope_key=request.scope_key,
                workspace_id=request.workspace_id,
                name=request.name,
                enabled=request.enabled,
                window_minutes=request.window_minutes,
                evaluation_interval_seconds=request.evaluation_interval_seconds,
                min_sample_size=request.min_sample_size,
                target_plan_success_rate_pct=request.target_plan_success_rate_pct,
                target_step_success_rate_pct=request.target_step_success_rate_pct,
                max_p95_plan_duration_ms=request.max_p95_plan_duration_ms,
                max_p95_step_duration_ms=request.max_p95_step_duration_ms,
                max_tool_failure_rate_pct=request.max_tool_failure_rate_pct,
                max_open_critical_incidents=request.max_open_critical_incidents,
                metadata_json=request.metadata,
            )
            session.add(row)
            session.flush()
            result = self._serialize_policy(row)
        self._dirty = True
        return result

    def list_policies(self) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = list(
                session.scalars(
                    select(ExecutionSLOPolicyModel).order_by(
                        ExecutionSLOPolicyModel.scope_key.asc()
                    )
                ).all()
            )
            return [self._serialize_policy(row) for row in rows]

    def get_policy(self, policy_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(ExecutionSLOPolicyModel, policy_id)
            return None if row is None else self._serialize_policy(row)

    def get_effective_policy(
        self,
        workspace_id: str | None,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = self._effective_policy_row(session, workspace_id)
            return None if row is None else self._serialize_policy(row)

    def update_policy(
        self,
        policy_id: str,
        request: ExecutionSLOPolicyUpdate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(ExecutionSLOPolicyModel, policy_id)
            if row is None:
                raise ExecutionSLOPolicyNotFound("SLO policy not found.")
            values = request.model_dump(exclude_unset=True)
            mapping = {
                "name": "name",
                "enabled": "enabled",
                "window_minutes": "window_minutes",
                "evaluation_interval_seconds": "evaluation_interval_seconds",
                "min_sample_size": "min_sample_size",
                "target_plan_success_rate_pct": "target_plan_success_rate_pct",
                "target_step_success_rate_pct": "target_step_success_rate_pct",
                "max_p95_plan_duration_ms": "max_p95_plan_duration_ms",
                "max_p95_step_duration_ms": "max_p95_step_duration_ms",
                "max_tool_failure_rate_pct": "max_tool_failure_rate_pct",
                "max_open_critical_incidents": "max_open_critical_incidents",
                "metadata": "metadata_json",
            }
            for source, target in mapping.items():
                if source in values:
                    setattr(row, target, values[source])
            row.updated_at = utc_now()
            session.flush()
            result = self._serialize_policy(row)
        self._dirty = True
        return result

    def dashboard(
        self,
        *,
        workspace_id: str | None = None,
        window_minutes: int = 60,
    ) -> dict[str, Any]:
        ended_at = utc_now()
        started_at = ended_at - timedelta(minutes=window_minutes)

        with self._session_factory() as session:
            plan_statement = select(ExecutionPlanModel).where(
                ExecutionPlanModel.created_at >= started_at
            )
            if workspace_id is not None:
                plan_statement = plan_statement.where(
                    ExecutionPlanModel.workspace_id == workspace_id
                )
            plans = list(session.scalars(plan_statement).all())

            current_plan_statement = select(ExecutionPlanModel).where(
                ExecutionPlanModel.status == "running"
            )
            if workspace_id is not None:
                current_plan_statement = current_plan_statement.where(
                    ExecutionPlanModel.workspace_id == workspace_id
                )
            active_plans = list(session.scalars(current_plan_statement).all())

            plan_ids = [row.id for row in plans]
            run_statement = select(ExecutionStepRunModel).where(
                ExecutionStepRunModel.created_at >= started_at
            )
            if workspace_id is not None:
                if plan_ids:
                    run_statement = run_statement.where(
                        ExecutionStepRunModel.plan_id.in_(plan_ids)
                    )
                else:
                    step_runs = []
                    run_statement = None
            if run_statement is not None:
                step_runs = list(session.scalars(run_statement).all())

            tool_statement = select(ToolInvocationModel).where(
                ToolInvocationModel.created_at >= started_at
            )
            if workspace_id is not None:
                tool_statement = tool_statement.where(
                    ToolInvocationModel.workspace_id == workspace_id
                )
            tool_calls = list(session.scalars(tool_statement).all())

            planner_statement = select(ExecutionPlannerRunModel).where(
                ExecutionPlannerRunModel.created_at >= started_at
            )
            if workspace_id is not None:
                planner_statement = planner_statement.where(
                    ExecutionPlannerRunModel.workspace_id == workspace_id
                )
            planner_runs = list(session.scalars(planner_statement).all())

            review_statement = select(ExecutionPlanReviewModel).where(
                ExecutionPlanReviewModel.created_at >= started_at
            )
            if workspace_id is not None:
                review_statement = review_statement.where(
                    ExecutionPlanReviewModel.workspace_id == workspace_id
                )
            reviews = list(session.scalars(review_statement).all())

            incident_statement = select(ExecutionSupervisorIncidentModel).where(
                ExecutionSupervisorIncidentModel.status.in_(
                    ["open", "acknowledged"]
                )
            )
            if workspace_id is not None:
                incident_statement = incident_statement.where(
                    ExecutionSupervisorIncidentModel.workspace_id == workspace_id
                )
            open_incidents = list(session.scalars(incident_statement).all())

            agent_statement = select(AgentProfileModel)
            if workspace_id is not None:
                agent_statement = agent_statement.where(
                    (AgentProfileModel.workspace_id == workspace_id)
                    | (AgentProfileModel.workspace_id.is_(None))
                )
            agents = list(session.scalars(agent_statement).all())

            breach_statement = select(ExecutionSLOBreachModel).where(
                ExecutionSLOBreachModel.status == "open"
            )
            if workspace_id is not None:
                breach_statement = breach_statement.where(
                    ExecutionSLOBreachModel.workspace_id == workspace_id
                )
            open_breaches = list(session.scalars(breach_statement).all())

        plan_statuses = Counter(row.status for row in plans)
        plan_terminal = plan_statuses["completed"] + plan_statuses["failed"]
        plan_durations = []
        for row in plans:
            started = ensure_utc(row.started_at)
            finished = ensure_utc(row.finished_at)
            if started is not None and finished is not None:
                plan_durations.append((finished - started).total_seconds() * 1000)

        step_statuses = Counter(row.status for row in step_runs)
        step_terminal = step_statuses["completed"] + step_statuses["failed"]
        step_durations = [
            float(row.duration_ms)
            for row in step_runs
            if row.duration_ms is not None
        ]
        retry_attempts = sum(1 for row in step_runs if int(row.attempt) > 1)

        tool_statuses = Counter(row.status for row in tool_calls)
        tool_terminal = sum(
            tool_statuses[name]
            for name in (
                "completed",
                "failed",
                "denied",
                "timed_out",
                "cancelled",
            )
        )
        tool_failures = sum(
            tool_statuses[name] for name in ("failed", "denied", "timed_out")
        )
        tool_durations = [
            float(row.duration_ms)
            for row in tool_calls
            if row.duration_ms is not None
        ]

        planner_statuses = Counter(row.status for row in planner_runs)
        review_decisions = Counter(row.decision for row in reviews if row.decision)
        agent_statuses = Counter(row.status for row in agents)
        incident_severities = Counter(row.severity for row in open_incidents)
        breach_severities = Counter(row.severity for row in open_breaches)

        plan_success_rate = percentage(plan_statuses["completed"], plan_terminal)
        step_success_rate = percentage(step_statuses["completed"], step_terminal)
        tool_failure_rate = percentage(tool_failures, tool_terminal)

        health = "healthy"
        if incident_severities["critical"] or breach_severities["critical"]:
            health = "critical"
        elif open_incidents or open_breaches or plan_statuses["failed"]:
            health = "degraded"

        return {
            "scope": {
                "scope_key": (
                    "global" if workspace_id is None else f"workspace:{workspace_id}"
                ),
                "workspace_id": workspace_id,
                "window_minutes": window_minutes,
                "window_started_at": iso(started_at),
                "window_ended_at": iso(ended_at),
            },
            "health": health,
            "plans": {
                "total": len(plans),
                "active": len(active_plans),
                "by_status": dict(plan_statuses),
                "terminal_sample_size": plan_terminal,
                "success_rate_pct": plan_success_rate,
                "p50_duration_ms": percentile(plan_durations, 50),
                "p95_duration_ms": percentile(plan_durations, 95),
            },
            "steps": {
                "runs": len(step_runs),
                "by_status": dict(step_statuses),
                "terminal_sample_size": step_terminal,
                "success_rate_pct": step_success_rate,
                "retry_attempts": retry_attempts,
                "p50_duration_ms": percentile(step_durations, 50),
                "p95_duration_ms": percentile(step_durations, 95),
            },
            "tools": {
                "invocations": len(tool_calls),
                "by_status": dict(tool_statuses),
                "terminal_sample_size": tool_terminal,
                "failure_rate_pct": tool_failure_rate,
                "p50_duration_ms": percentile(tool_durations, 50),
                "p95_duration_ms": percentile(tool_durations, 95),
            },
            "planner": {
                "runs": len(planner_runs),
                "by_status": dict(planner_statuses),
                "failure_rate_pct": percentage(
                    planner_statuses["failed"],
                    planner_statuses["completed"] + planner_statuses["failed"],
                ),
            },
            "reviews": {
                "total": len(reviews),
                "by_decision": dict(review_decisions),
                "pass_rate_pct": percentage(
                    review_decisions["pass"],
                    sum(review_decisions.values()),
                ),
            },
            "agents": {
                "registered": len(agents),
                "by_status": dict(agent_statuses),
                "available": agent_statuses["available"],
            },
            "incidents": {
                "open": len(open_incidents),
                "by_severity": dict(incident_severities),
                "critical": incident_severities["critical"],
            },
            "slo": {
                "open_breaches": len(open_breaches),
                "by_severity": dict(breach_severities),
            },
        }

    async def collect_snapshot(
        self,
        *,
        workspace_id: str | None = None,
        window_minutes: int = 60,
        persist: bool = True,
    ) -> dict[str, Any]:
        dashboard = self.dashboard(
            workspace_id=workspace_id,
            window_minutes=window_minutes,
        )
        snapshot = None
        if persist:
            snapshot = self._persist_snapshot(dashboard)
            await self._event_bus.publish(
                Event(
                    event_type="execution_observability.snapshot.created",
                    source="execution_observability",
                    workspace_id=workspace_id,
                    correlation_id=snapshot["id"],
                    payload={
                        "snapshot_id": snapshot["id"],
                        "scope_key": snapshot["scope_key"],
                        "window_started_at": snapshot["window_started_at"],
                        "window_ended_at": snapshot["window_ended_at"],
                    },
                )
            )
        self._collections += 1
        self._last_collection_at = utc_now()
        self._dirty = False
        return {"dashboard": dashboard, "snapshot": snapshot}

    async def evaluate_slos(
        self,
        *,
        workspace_id: str | None = None,
        policy_id: str | None = None,
        persist_snapshot: bool = True,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            statement = select(ExecutionSLOPolicyModel).where(
                ExecutionSLOPolicyModel.enabled.is_(True)
            )
            if policy_id is not None:
                statement = statement.where(ExecutionSLOPolicyModel.id == policy_id)
            elif workspace_id is not None:
                policy = self._effective_policy_row(session, workspace_id)
                policies = [] if policy is None else [self._serialize_policy(policy)]
                rows = []
            else:
                rows = list(session.scalars(statement).all())
                policies = [self._serialize_policy(row) for row in rows]
            if policy_id is not None:
                rows = list(session.scalars(statement).all())
                policies = [self._serialize_policy(row) for row in rows]

        results: list[dict[str, Any]] = []
        for policy in policies:
            dashboard = self.dashboard(
                workspace_id=policy["workspace_id"],
                window_minutes=int(policy["window_minutes"]),
            )
            snapshot = self._persist_snapshot(dashboard) if persist_snapshot else None
            result = await self._evaluate_policy(policy, dashboard)
            result["snapshot"] = snapshot
            results.append(result)

        self._evaluations += 1
        self._last_evaluation_at = utc_now()
        self._dirty = False
        return {"evaluated": len(results), "results": results}

    def list_snapshots(
        self,
        *,
        workspace_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        statement = select(ExecutionMetricSnapshotModel)
        if workspace_id is not None:
            statement = statement.where(
                ExecutionMetricSnapshotModel.workspace_id == workspace_id
            )
        statement = (
            statement.order_by(ExecutionMetricSnapshotModel.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        with self._session_factory() as session:
            return [
                self._serialize_snapshot(row)
                for row in session.scalars(statement).all()
            ]

    def list_breaches(
        self,
        *,
        workspace_id: str | None = None,
        policy_id: str | None = None,
        status: str | None = None,
        severity: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        statement = select(ExecutionSLOBreachModel)
        if workspace_id is not None:
            statement = statement.where(
                ExecutionSLOBreachModel.workspace_id == workspace_id
            )
        if policy_id is not None:
            statement = statement.where(ExecutionSLOBreachModel.policy_id == policy_id)
        if status is not None:
            statement = statement.where(ExecutionSLOBreachModel.status == status)
        if severity is not None:
            statement = statement.where(ExecutionSLOBreachModel.severity == severity)
        statement = (
            statement.order_by(ExecutionSLOBreachModel.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        with self._session_factory() as session:
            return [
                self._serialize_breach(row)
                for row in session.scalars(statement).all()
            ]

    async def resolve_breach(
        self,
        breach_id: str,
        *,
        actor_id: str,
        reason: str,
        dismissed: bool = False,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(ExecutionSLOBreachModel, breach_id)
            if row is None:
                raise ExecutionSLOBreachNotFound("SLO breach not found.")
            row.status = "dismissed" if dismissed else "resolved"
            row.resolved_at = utc_now()
            row.resolved_by = actor_id
            row.resolution = reason
            row.updated_at = utc_now()
            session.flush()
            result = self._serialize_breach(row)

        await self._event_bus.publish(
            Event(
                event_type="execution_observability.slo_breach.resolved",
                source="execution_observability",
                workspace_id=result.get("workspace_id"),
                correlation_id=result["id"],
                payload={
                    "breach_id": result["id"],
                    "metric_name": result["metric_name"],
                    "actor_id": actor_id,
                    "reason": reason,
                    "dismissed": dismissed,
                },
            )
        )
        return result

    def prometheus_metrics(
        self,
        *,
        workspace_id: str | None = None,
        window_minutes: int = 60,
    ) -> str:
        dashboard = self.dashboard(
            workspace_id=workspace_id,
            window_minutes=window_minutes,
        )
        scope = dashboard["scope"]["scope_key"].replace('"', "")
        label = f'scope="{scope}"'
        plan_p95 = dashboard["plans"]["p95_duration_ms"] or 0
        step_p95 = dashboard["steps"]["p95_duration_ms"] or 0
        lines = [
            "# HELP ai_studio_execution_plans_total Plans observed in the window.",
            "# TYPE ai_studio_execution_plans_total gauge",
            f"ai_studio_execution_plans_total{{{label}}} {dashboard['plans']['total']}",
            "# HELP ai_studio_execution_plans_active Currently running plans.",
            "# TYPE ai_studio_execution_plans_active gauge",
            f"ai_studio_execution_plans_active{{{label}}} {dashboard['plans']['active']}",
            "# HELP ai_studio_execution_plan_success_rate_percent Plan success rate.",
            "# TYPE ai_studio_execution_plan_success_rate_percent gauge",
            f"ai_studio_execution_plan_success_rate_percent{{{label}}} {dashboard['plans']['success_rate_pct']}",
            "# HELP ai_studio_execution_plan_duration_p95_ms P95 plan duration.",
            "# TYPE ai_studio_execution_plan_duration_p95_ms gauge",
            f"ai_studio_execution_plan_duration_p95_ms{{{label}}} {plan_p95}",
            "# HELP ai_studio_execution_step_success_rate_percent Step success rate.",
            "# TYPE ai_studio_execution_step_success_rate_percent gauge",
            f"ai_studio_execution_step_success_rate_percent{{{label}}} {dashboard['steps']['success_rate_pct']}",
            "# HELP ai_studio_execution_step_duration_p95_ms P95 step duration.",
            "# TYPE ai_studio_execution_step_duration_p95_ms gauge",
            f"ai_studio_execution_step_duration_p95_ms{{{label}}} {step_p95}",
            "# HELP ai_studio_tool_failure_rate_percent Tool failure rate.",
            "# TYPE ai_studio_tool_failure_rate_percent gauge",
            f"ai_studio_tool_failure_rate_percent{{{label}}} {dashboard['tools']['failure_rate_pct']}",
            "# HELP ai_studio_execution_open_critical_incidents Open critical incidents.",
            "# TYPE ai_studio_execution_open_critical_incidents gauge",
            f"ai_studio_execution_open_critical_incidents{{{label}}} {dashboard['incidents']['critical']}",
            "# HELP ai_studio_execution_open_slo_breaches Open SLO breaches.",
            "# TYPE ai_studio_execution_open_slo_breaches gauge",
            f"ai_studio_execution_open_slo_breaches{{{label}}} {dashboard['slo']['open_breaches']}",
        ]
        return "\n".join(lines) + "\n"

    async def _evaluate_policy(
        self,
        policy: dict[str, Any],
        dashboard: dict[str, Any],
    ) -> dict[str, Any]:
        checks = [
            {
                "metric_name": "plan_success_rate_pct",
                "actual": dashboard["plans"]["success_rate_pct"],
                "target": policy["target_plan_success_rate_pct"],
                "comparison": "minimum",
                "sample_size": dashboard["plans"]["terminal_sample_size"],
                "severity": "critical",
            },
            {
                "metric_name": "step_success_rate_pct",
                "actual": dashboard["steps"]["success_rate_pct"],
                "target": policy["target_step_success_rate_pct"],
                "comparison": "minimum",
                "sample_size": dashboard["steps"]["terminal_sample_size"],
                "severity": "high",
            },
            {
                "metric_name": "p95_plan_duration_ms",
                "actual": dashboard["plans"]["p95_duration_ms"],
                "target": policy["max_p95_plan_duration_ms"],
                "comparison": "maximum",
                "sample_size": dashboard["plans"]["terminal_sample_size"],
                "severity": "high",
            },
            {
                "metric_name": "p95_step_duration_ms",
                "actual": dashboard["steps"]["p95_duration_ms"],
                "target": policy["max_p95_step_duration_ms"],
                "comparison": "maximum",
                "sample_size": dashboard["steps"]["terminal_sample_size"],
                "severity": "warning",
            },
            {
                "metric_name": "tool_failure_rate_pct",
                "actual": dashboard["tools"]["failure_rate_pct"],
                "target": policy["max_tool_failure_rate_pct"],
                "comparison": "maximum",
                "sample_size": dashboard["tools"]["terminal_sample_size"],
                "severity": "high",
            },
            {
                "metric_name": "open_critical_incidents",
                "actual": dashboard["incidents"]["critical"],
                "target": policy["max_open_critical_incidents"],
                "comparison": "maximum",
                "sample_size": dashboard["incidents"]["open"],
                "severity": "critical",
                "always_evaluate": True,
            },
        ]

        outcomes: list[dict[str, Any]] = []
        breached_events: list[dict[str, Any]] = []
        recovered_events: list[dict[str, Any]] = []
        started_at = datetime.fromisoformat(
            dashboard["scope"]["window_started_at"]
        )
        ended_at = datetime.fromisoformat(dashboard["scope"]["window_ended_at"])

        with self._session_factory() as session:
            for check in checks:
                actual = check["actual"]
                enough_samples = (
                    bool(check.get("always_evaluate"))
                    or int(check["sample_size"]) >= int(policy["min_sample_size"])
                )
                if actual is None or not enough_samples:
                    outcomes.append(
                        {
                            **check,
                            "status": "insufficient_data",
                            "breach_id": None,
                        }
                    )
                    continue

                actual_float = float(actual)
                target_float = float(check["target"])
                violated = (
                    actual_float < target_float
                    if check["comparison"] == "minimum"
                    else actual_float > target_float
                )
                dedupe_key = (
                    f"{policy['scope_key']}|{policy['id']}|{check['metric_name']}"
                )
                row = session.scalar(
                    select(ExecutionSLOBreachModel).where(
                        ExecutionSLOBreachModel.dedupe_key == dedupe_key
                    )
                )
                if violated:
                    created = row is None
                    if row is None:
                        row = ExecutionSLOBreachModel(
                            policy_id=policy["id"],
                            scope_key=policy["scope_key"],
                            workspace_id=policy["workspace_id"],
                            metric_name=check["metric_name"],
                            status="open",
                            severity=check["severity"],
                            comparison=check["comparison"],
                            dedupe_key=dedupe_key,
                            target_value=target_float,
                            actual_value=actual_float,
                            sample_size=int(check["sample_size"]),
                            window_started_at=started_at,
                            window_ended_at=ended_at,
                            details_json={"dashboard_health": dashboard["health"]},
                        )
                        session.add(row)
                    else:
                        row.status = "open"
                        row.severity = check["severity"]
                        row.target_value = target_float
                        row.actual_value = actual_float
                        row.sample_size = int(check["sample_size"])
                        row.window_started_at = started_at
                        row.window_ended_at = ended_at
                        row.last_detected_at = utc_now()
                        row.occurrence_count += 1
                        row.resolved_at = None
                        row.resolved_by = None
                        row.resolution = None
                        row.updated_at = utc_now()
                    session.flush()
                    serialized = self._serialize_breach(row)
                    breached_events.append({"created": created, **serialized})
                    outcomes.append(
                        {**check, "status": "breached", "breach_id": row.id}
                    )
                else:
                    recovered_id = None
                    if row is not None and row.status == "open":
                        row.status = "resolved"
                        row.resolved_at = utc_now()
                        row.resolved_by = "execution-observability"
                        row.resolution = "Metric returned within its SLO target."
                        row.updated_at = utc_now()
                        session.flush()
                        recovered_id = row.id
                        recovered_events.append(self._serialize_breach(row))
                    outcomes.append(
                        {**check, "status": "met", "breach_id": recovered_id}
                    )

        for breach in breached_events:
            await self._event_bus.publish(
                Event(
                    event_type="execution_observability.slo_breached",
                    source="execution_observability",
                    workspace_id=breach.get("workspace_id"),
                    correlation_id=breach["id"],
                    payload={
                        "breach_id": breach["id"],
                        "policy_id": breach["policy_id"],
                        "metric_name": breach["metric_name"],
                        "severity": breach["severity"],
                        "target_value": breach["target_value"],
                        "actual_value": breach["actual_value"],
                        "created": breach["created"],
                    },
                )
            )
        for breach in recovered_events:
            await self._event_bus.publish(
                Event(
                    event_type="execution_observability.slo_recovered",
                    source="execution_observability",
                    workspace_id=breach.get("workspace_id"),
                    correlation_id=breach["id"],
                    payload={
                        "breach_id": breach["id"],
                        "policy_id": breach["policy_id"],
                        "metric_name": breach["metric_name"],
                    },
                )
            )

        return {
            "policy": policy,
            "dashboard": dashboard,
            "checks": outcomes,
            "breaches": sum(1 for row in outcomes if row["status"] == "breached"),
            "met": sum(1 for row in outcomes if row["status"] == "met"),
            "insufficient_data": sum(
                1 for row in outcomes if row["status"] == "insufficient_data"
            ),
        }

    def _persist_snapshot(self, dashboard: dict[str, Any]) -> dict[str, Any]:
        scope = dashboard["scope"]
        with self._session_factory() as session:
            row = ExecutionMetricSnapshotModel(
                scope_key=scope["scope_key"],
                workspace_id=scope["workspace_id"],
                window_started_at=datetime.fromisoformat(
                    scope["window_started_at"]
                ),
                window_ended_at=datetime.fromisoformat(scope["window_ended_at"]),
                metrics_json=dashboard,
            )
            session.add(row)
            session.flush()
            return self._serialize_snapshot(row)

    def _ensure_default_policy(self) -> None:
        with self._session_factory() as session:
            row = session.scalar(
                select(ExecutionSLOPolicyModel).where(
                    ExecutionSLOPolicyModel.scope_key == "global"
                )
            )
            if row is None:
                session.add(
                    ExecutionSLOPolicyModel(
                        scope_key="global",
                        workspace_id=None,
                        name="Default orchestration SLO",
                        enabled=True,
                        window_minutes=60,
                        evaluation_interval_seconds=60,
                        min_sample_size=5,
                        target_plan_success_rate_pct=95.0,
                        target_step_success_rate_pct=97.0,
                        max_p95_plan_duration_ms=600000.0,
                        max_p95_step_duration_ms=300000.0,
                        max_tool_failure_rate_pct=5.0,
                        max_open_critical_incidents=0,
                        metadata_json={"seeded": True},
                    )
                )
                session.flush()

    def _effective_policy_row(
        self,
        session: Session,
        workspace_id: str | None,
    ) -> ExecutionSLOPolicyModel | None:
        if workspace_id is not None:
            row = session.scalar(
                select(ExecutionSLOPolicyModel).where(
                    ExecutionSLOPolicyModel.scope_key
                    == f"workspace:{workspace_id}"
                )
            )
            if row is not None:
                return row
        return session.scalar(
            select(ExecutionSLOPolicyModel).where(
                ExecutionSLOPolicyModel.scope_key == "global"
            )
        )

    async def _monitor_loop(self) -> None:
        while self._running:
            try:
                interval = self._next_interval_seconds()
                await asyncio.sleep(interval)
                if not self._running:
                    break
                if self._dirty:
                    await self.evaluate_slos(persist_snapshot=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._evaluation_failures += 1
                await asyncio.sleep(5)

    def _next_interval_seconds(self) -> int:
        with self._session_factory() as session:
            values = list(
                session.scalars(
                    select(ExecutionSLOPolicyModel.evaluation_interval_seconds)
                    .where(ExecutionSLOPolicyModel.enabled.is_(True))
                ).all()
            )
        return max(5, min(values) if values else 60)

    @staticmethod
    def _serialize_policy(row: ExecutionSLOPolicyModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "scope_key": row.scope_key,
            "workspace_id": row.workspace_id,
            "name": row.name,
            "enabled": row.enabled,
            "window_minutes": row.window_minutes,
            "evaluation_interval_seconds": row.evaluation_interval_seconds,
            "min_sample_size": row.min_sample_size,
            "target_plan_success_rate_pct": row.target_plan_success_rate_pct,
            "target_step_success_rate_pct": row.target_step_success_rate_pct,
            "max_p95_plan_duration_ms": row.max_p95_plan_duration_ms,
            "max_p95_step_duration_ms": row.max_p95_step_duration_ms,
            "max_tool_failure_rate_pct": row.max_tool_failure_rate_pct,
            "max_open_critical_incidents": row.max_open_critical_incidents,
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _serialize_snapshot(row: ExecutionMetricSnapshotModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "scope_key": row.scope_key,
            "workspace_id": row.workspace_id,
            "window_started_at": iso(row.window_started_at),
            "window_ended_at": iso(row.window_ended_at),
            "metrics": dict(row.metrics_json or {}),
            "created_at": iso(row.created_at),
        }

    @staticmethod
    def _serialize_breach(row: ExecutionSLOBreachModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "policy_id": row.policy_id,
            "scope_key": row.scope_key,
            "workspace_id": row.workspace_id,
            "metric_name": row.metric_name,
            "status": row.status,
            "severity": row.severity,
            "comparison": row.comparison,
            "target_value": row.target_value,
            "actual_value": row.actual_value,
            "sample_size": row.sample_size,
            "window_started_at": iso(row.window_started_at),
            "window_ended_at": iso(row.window_ended_at),
            "details": dict(row.details_json or {}),
            "occurrence_count": row.occurrence_count,
            "first_detected_at": iso(row.first_detected_at),
            "last_detected_at": iso(row.last_detected_at),
            "resolved_at": iso(row.resolved_at),
            "resolved_by": row.resolved_by,
            "resolution": row.resolution,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }
