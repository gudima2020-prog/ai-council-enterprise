from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.events import Event, EventBus
from backend.database.session import session_scope
from backend.orchestration.enums import (
    ExecutionPlanStatus,
    ExecutionStepStatus,
)
from backend.orchestration.models import (
    ExecutionPlanModel,
    ExecutionPlanStepModel,
    ExecutionSupervisorActionModel,
    ExecutionSupervisorIncidentModel,
    ExecutionSupervisorPolicyModel,
)
from backend.orchestration.planner import (
    ExecutionPlannerError,
    ExecutionPlannerService,
)
from backend.orchestration.planner_schemas import ExecutionPlanReplanRequest
from backend.orchestration.runtime import (
    ExecutionPlanRuntime,
    ExecutionRuntimeError,
)
from backend.orchestration.supervisor_schemas import (
    SupervisorActionType,
    SupervisorInterventionRequest,
    SupervisorPolicyCreate,
    SupervisorPolicyUpdate,
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


SessionContextFactory = Callable[[], AbstractContextManager[Session]]


class ExecutionSupervisorError(RuntimeError):
    pass


class SupervisorPolicyNotFound(ExecutionSupervisorError):
    pass


class SupervisorIncidentNotFound(ExecutionSupervisorError):
    pass


class ExecutionSupervisorService:
    """Monitors execution plans and applies explicit intervention policies.

    Automatic intervention is disabled in the seeded global policy. The
    supervisor always records incidents; destructive actions require either a
    policy with ``automatic_actions_enabled`` or a manual API request.
    """

    TERMINAL_PLAN_STATUSES = {
        ExecutionPlanStatus.COMPLETED.value,
        ExecutionPlanStatus.CANCELLED.value,
        ExecutionPlanStatus.SUPERSEDED.value,
    }

    def __init__(
        self,
        *,
        event_bus: EventBus,
        runtime: ExecutionPlanRuntime,
        planner: ExecutionPlannerService,
        session_factory: SessionContextFactory = session_scope,
    ) -> None:
        self._event_bus = event_bus
        self._runtime = runtime
        self._planner = planner
        self._session_factory = session_factory
        self._monitor_task: asyncio.Task[None] | None = None
        self._action_tasks: set[asyncio.Task[Any]] = set()
        self._running = False
        self._events_seen = 0
        self._scans = 0
        self._incidents_created = 0
        self._actions_completed = 0
        self._actions_failed = 0
        self._last_scan_at: datetime | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._ensure_default_policy()
        self._running = True
        self._monitor_task = asyncio.create_task(
            self._monitor_loop(),
            name="execution-supervisor-monitor",
        )

    async def shutdown(self) -> None:
        self._running = False
        monitor = self._monitor_task
        self._monitor_task = None
        if monitor is not None:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)

        action_tasks = list(self._action_tasks)
        for task in action_tasks:
            task.cancel()
        if action_tasks:
            await asyncio.gather(*action_tasks, return_exceptions=True)
        self._action_tasks.clear()

    def stats(self) -> dict[str, Any]:
        with self._session_factory() as session:
            policies = len(
                list(session.scalars(select(ExecutionSupervisorPolicyModel)).all())
            )
            incidents = list(
                session.scalars(select(ExecutionSupervisorIncidentModel)).all()
            )
            actions = list(
                session.scalars(select(ExecutionSupervisorActionModel)).all()
            )

        return {
            "running": self._running,
            "events_seen": self._events_seen,
            "scans": self._scans,
            "incidents_created": self._incidents_created,
            "actions_completed": self._actions_completed,
            "actions_failed": self._actions_failed,
            "active_action_tasks": sum(
                1 for task in self._action_tasks if not task.done()
            ),
            "last_scan_at": iso(self._last_scan_at),
            "policies": policies,
            "open_incidents": sum(
                1
                for row in incidents
                if row.status in {"open", "acknowledged"}
            ),
            "actions": len(actions),
            "capabilities": [
                "event_monitoring",
                "stall_detection",
                "long_running_step_detection",
                "retry_threshold_detection",
                "manual_intervention",
                "policy_controlled_cancel",
                "policy_controlled_replan",
            ],
        }

    async def handle_event(self, event: Event) -> None:
        self._events_seen += 1
        event_type = event.event_type
        payload = dict(event.payload or {})
        plan_id = str(payload.get("plan_id") or event.correlation_id or "")
        if not plan_id:
            return

        if event_type in {
            "execution_plan.runtime.completed",
            "execution_plan.runtime.cancelled",
        }:
            await self.resolve_plan_incidents(
                plan_id,
                actor_id="execution-supervisor",
                reason=f"Plan reached terminal event {event_type}.",
            )
            return

        if event_type == "execution_plan.planner.replanned":
            source_plan_id = str(payload.get("source_plan_id") or "")
            if source_plan_id:
                await self.resolve_plan_incidents(
                    source_plan_id,
                    actor_id="execution-supervisor",
                    reason="A replacement Execution Plan was created.",
                )
            return

        plan = self._plan_snapshot(plan_id)
        if plan is None:
            return
        policy = self.get_effective_policy(plan.get("workspace_id"))
        if policy is None or not policy["enabled"]:
            return

        if event_type == "execution_plan.step.retrying":
            attempt = int(payload.get("attempt") or 0)
            if attempt >= int(policy["retry_warning_threshold"]):
                await self._detect_incident(
                    plan=plan,
                    policy=policy,
                    step_id=str(payload.get("step_id") or "") or None,
                    incident_type="retry_threshold",
                    severity="warning",
                    title="Step retry threshold reached",
                    message=(
                        f"Step {payload.get('step_key') or payload.get('step_id')} "
                        f"reached attempt {attempt}."
                    ),
                    details={"event": event.to_dict()},
                    requested_action=str(policy["retry_action"]),
                )
            return

        if event_type == "execution_plan.step.failed":
            await self._detect_incident(
                plan=plan,
                policy=policy,
                step_id=str(payload.get("step_id") or "") or None,
                incident_type="step_failed",
                severity="high",
                title="Execution step failed",
                message=str(
                    payload.get("message")
                    or "An Execution Plan step failed."
                ),
                details={"event": event.to_dict()},
                requested_action=SupervisorActionType.OBSERVE.value,
            )
            return

        if event_type == "execution_plan.runtime.failed":
            # The Planner Service can already own automatic replanning. In that
            # case the supervisor records the failure but avoids a second replan.
            planner_meta = dict(
                (plan.get("metadata") or {}).get("_planner") or {}
            )
            action = str(policy["failure_action"])
            if planner_meta.get("auto_replan") is True and action == "replan":
                action = SupervisorActionType.OBSERVE.value

            await self._detect_incident(
                plan=plan,
                policy=policy,
                step_id=None,
                incident_type="plan_failed",
                severity="critical",
                title="Execution Plan failed",
                message=str(
                    payload.get("message")
                    or payload.get("error")
                    or "Execution Plan runtime failed."
                ),
                details={"event": event.to_dict()},
                requested_action=action,
            )

    async def scan_once(self) -> dict[str, Any]:
        now = utc_now()
        snapshots: list[dict[str, Any]] = []

        with self._session_factory() as session:
            plans = list(
                session.scalars(
                    select(ExecutionPlanModel).where(
                        ExecutionPlanModel.status
                        == ExecutionPlanStatus.RUNNING.value
                    )
                ).all()
            )
            for plan in plans:
                policy_row = self._effective_policy_row(
                    session,
                    plan.workspace_id,
                )
                if policy_row is None or not policy_row.enabled:
                    continue
                steps = list(
                    session.scalars(
                        select(ExecutionPlanStepModel).where(
                            ExecutionPlanStepModel.plan_id == plan.id
                        )
                    ).all()
                )
                snapshots.append(
                    {
                        "plan": self._serialize_plan(plan),
                        "policy": self._serialize_policy(policy_row),
                        "steps": [self._serialize_step(step) for step in steps],
                    }
                )

        detected = 0
        for snapshot in snapshots:
            plan = snapshot["plan"]
            policy = snapshot["policy"]
            steps = snapshot["steps"]
            activity_values = [
                ensure_utc(plan.get("started_at_raw")),
                ensure_utc(plan.get("updated_at_raw")),
            ]
            for step in steps:
                activity_values.extend(
                    [
                        ensure_utc(step.get("started_at_raw")),
                        ensure_utc(step.get("finished_at_raw")),
                        ensure_utc(step.get("updated_at_raw")),
                    ]
                )
            activity_values = [value for value in activity_values if value]
            latest_activity = max(activity_values) if activity_values else now
            stalled_seconds = max(0.0, (now - latest_activity).total_seconds())

            if stalled_seconds >= int(policy["stall_timeout_seconds"]):
                await self._detect_incident(
                    plan=plan,
                    policy=policy,
                    step_id=None,
                    incident_type="stalled",
                    severity="critical",
                    title="Execution Plan appears stalled",
                    message=(
                        "No plan activity was recorded for "
                        f"{int(stalled_seconds)} seconds."
                    ),
                    details={
                        "stalled_seconds": stalled_seconds,
                        "latest_activity_at": latest_activity.isoformat(),
                    },
                    requested_action=str(policy["stall_action"]),
                )
                detected += 1

            for step in steps:
                if step["status"] != ExecutionStepStatus.RUNNING.value:
                    continue
                started_at = ensure_utc(step.get("started_at_raw"))
                if started_at is None:
                    continue
                runtime_seconds = max(0.0, (now - started_at).total_seconds())
                if runtime_seconds < int(policy["max_step_runtime_seconds"]):
                    continue
                await self._detect_incident(
                    plan=plan,
                    policy=policy,
                    step_id=step["id"],
                    incident_type="long_running",
                    severity="high",
                    title="Execution step is running too long",
                    message=(
                        f"Step {step['step_key']} has been running for "
                        f"{int(runtime_seconds)} seconds."
                    ),
                    details={
                        "runtime_seconds": runtime_seconds,
                        "started_at": started_at.isoformat(),
                        "step_key": step["step_key"],
                    },
                    requested_action=str(policy["long_running_action"]),
                )
                detected += 1

        self._scans += 1
        self._last_scan_at = now
        return {
            "scanned_plans": len(snapshots),
            "detected_conditions": detected,
            "scanned_at": now.isoformat(),
        }

    def create_policy(
        self,
        request: SupervisorPolicyCreate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            existing = session.scalar(
                select(ExecutionSupervisorPolicyModel).where(
                    ExecutionSupervisorPolicyModel.scope_key
                    == request.scope_key
                )
            )
            if existing is not None:
                raise ExecutionSupervisorError(
                    f"Supervisor policy {request.scope_key} already exists."
                )
            row = ExecutionSupervisorPolicyModel(
                **self._policy_values(request.model_dump(mode="json"))
            )
            session.add(row)
            session.flush()
            return self._serialize_policy(row)

    def update_policy(
        self,
        policy_id: str,
        request: SupervisorPolicyUpdate,
    ) -> dict[str, Any]:
        values = {
            key: value
            for key, value in request.model_dump(
                mode="json",
                exclude_unset=True,
            ).items()
            if value is not None
        }
        with self._session_factory() as session:
            row = session.get(ExecutionSupervisorPolicyModel, policy_id)
            if row is None:
                raise SupervisorPolicyNotFound(
                    "Supervisor policy not found."
                )
            for key, value in self._policy_values(values).items():
                setattr(row, key, value)
            row.updated_at = utc_now()
            session.flush()
            return self._serialize_policy(row)

    def list_policies(self) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = list(
                session.scalars(
                    select(ExecutionSupervisorPolicyModel).order_by(
                        ExecutionSupervisorPolicyModel.scope_key
                    )
                ).all()
            )
            return [self._serialize_policy(row) for row in rows]

    def get_effective_policy(
        self,
        workspace_id: str | None,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = self._effective_policy_row(session, workspace_id)
            return None if row is None else self._serialize_policy(row)

    def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(ExecutionSupervisorIncidentModel, incident_id)
            return None if row is None else self._serialize_incident(row)

    def list_incidents(
        self,
        *,
        plan_id: str | None = None,
        workspace_id: str | None = None,
        status: str | None = None,
        severity: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        statement = select(ExecutionSupervisorIncidentModel)
        if plan_id is not None:
            statement = statement.where(
                ExecutionSupervisorIncidentModel.plan_id == plan_id
            )
        if workspace_id is not None:
            statement = statement.where(
                ExecutionSupervisorIncidentModel.workspace_id == workspace_id
            )
        if status is not None:
            statement = statement.where(
                ExecutionSupervisorIncidentModel.status == status
            )
        if severity is not None:
            statement = statement.where(
                ExecutionSupervisorIncidentModel.severity == severity
            )
        statement = (
            statement.order_by(
                ExecutionSupervisorIncidentModel.detected_at.desc()
            )
            .offset(offset)
            .limit(limit)
        )
        with self._session_factory() as session:
            return [
                self._serialize_incident(row)
                for row in session.scalars(statement).all()
            ]

    def list_actions(
        self,
        *,
        plan_id: str | None = None,
        incident_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        statement = select(ExecutionSupervisorActionModel)
        if plan_id is not None:
            statement = statement.where(
                ExecutionSupervisorActionModel.plan_id == plan_id
            )
        if incident_id is not None:
            statement = statement.where(
                ExecutionSupervisorActionModel.incident_id == incident_id
            )
        if status is not None:
            statement = statement.where(
                ExecutionSupervisorActionModel.status == status
            )
        statement = (
            statement.order_by(
                ExecutionSupervisorActionModel.created_at.desc()
            )
            .offset(offset)
            .limit(limit)
        )
        with self._session_factory() as session:
            return [
                self._serialize_action(row)
                for row in session.scalars(statement).all()
            ]

    async def acknowledge_incident(
        self,
        incident_id: str,
        *,
        actor_id: str,
        reason: str,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(ExecutionSupervisorIncidentModel, incident_id)
            if row is None:
                raise SupervisorIncidentNotFound("Supervisor incident not found.")
            if row.status in {"resolved", "dismissed"}:
                raise ExecutionSupervisorError(
                    f"Incident in status {row.status} cannot be acknowledged."
                )
            row.status = "acknowledged"
            row.acknowledged_at = utc_now()
            row.acknowledged_by = actor_id
            row.resolution = reason
            row.updated_at = utc_now()
            session.flush()
            result = self._serialize_incident(row)

        await self._event_bus.publish(
            Event(
                event_type="execution_plan.supervisor.incident.acknowledged",
                source="execution_supervisor",
                workspace_id=result.get("workspace_id"),
                correlation_id=result["plan_id"],
                payload={
                    "incident_id": incident_id,
                    "plan_id": result["plan_id"],
                    "actor_id": actor_id,
                    "reason": reason,
                },
            )
        )
        return result

    async def resolve_incident(
        self,
        incident_id: str,
        *,
        actor_id: str,
        reason: str,
        dismissed: bool = False,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(ExecutionSupervisorIncidentModel, incident_id)
            if row is None:
                raise SupervisorIncidentNotFound("Supervisor incident not found.")
            row.status = "dismissed" if dismissed else "resolved"
            row.resolved_at = utc_now()
            row.resolved_by = actor_id
            row.resolution = reason
            row.updated_at = utc_now()
            session.flush()
            result = self._serialize_incident(row)

        await self._event_bus.publish(
            Event(
                event_type="execution_plan.supervisor.incident.resolved",
                source="execution_supervisor",
                workspace_id=result.get("workspace_id"),
                correlation_id=result["plan_id"],
                payload={
                    "incident_id": incident_id,
                    "plan_id": result["plan_id"],
                    "actor_id": actor_id,
                    "reason": reason,
                    "dismissed": dismissed,
                },
            )
        )
        return result

    async def resolve_plan_incidents(
        self,
        plan_id: str,
        *,
        actor_id: str,
        reason: str,
    ) -> int:
        resolved_ids: list[str] = []
        with self._session_factory() as session:
            rows = list(
                session.scalars(
                    select(ExecutionSupervisorIncidentModel).where(
                        ExecutionSupervisorIncidentModel.plan_id == plan_id,
                        ExecutionSupervisorIncidentModel.status.in_(
                            ["open", "acknowledged"]
                        ),
                    )
                ).all()
            )
            for row in rows:
                row.status = "resolved"
                row.resolved_at = utc_now()
                row.resolved_by = actor_id
                row.resolution = reason
                row.updated_at = utc_now()
                resolved_ids.append(row.id)

        if resolved_ids:
            await self._event_bus.publish(
                Event(
                    event_type="execution_plan.supervisor.incidents.resolved",
                    source="execution_supervisor",
                    correlation_id=plan_id,
                    payload={
                        "plan_id": plan_id,
                        "incident_ids": resolved_ids,
                        "actor_id": actor_id,
                        "reason": reason,
                    },
                )
            )
        return len(resolved_ids)

    async def intervene(
        self,
        plan_id: str,
        request: SupervisorInterventionRequest,
        *,
        automatic: bool = False,
        policy_id: str | None = None,
    ) -> dict[str, Any] | None:
        plan = self._plan_snapshot(plan_id)
        if plan is None:
            return None

        dedupe_key = None
        if request.idempotency_key:
            prefix = "auto" if automatic else "manual"
            dedupe_key = f"{prefix}:{plan_id}:{request.idempotency_key}"

        with self._session_factory() as session:
            if dedupe_key:
                existing = session.scalar(
                    select(ExecutionSupervisorActionModel).where(
                        ExecutionSupervisorActionModel.dedupe_key == dedupe_key
                    )
                )
                if existing is not None:
                    action_id = existing.id
                else:
                    action_id = self._create_action_row(
                        session,
                        plan=plan,
                        request=request,
                        automatic=automatic,
                        policy_id=policy_id,
                        dedupe_key=dedupe_key,
                    ).id
            else:
                action_id = self._create_action_row(
                    session,
                    plan=plan,
                    request=request,
                    automatic=automatic,
                    policy_id=policy_id,
                    dedupe_key=None,
                ).id

        if request.wait:
            await self._execute_action(action_id)
        else:
            self._schedule_action(action_id)
        return self.get_action(action_id)

    def get_action(self, action_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(ExecutionSupervisorActionModel, action_id)
            return None if row is None else self._serialize_action(row)

    def plan_overview(self, plan_id: str) -> dict[str, Any] | None:
        plan = self._plan_snapshot(plan_id)
        if plan is None:
            return None
        public_plan = {
            key: value
            for key, value in plan.items()
            if not key.endswith("_raw")
        }
        return {
            "plan": public_plan,
            "effective_policy": self.get_effective_policy(
                plan.get("workspace_id")
            ),
            "incidents": self.list_incidents(plan_id=plan_id, limit=500),
            "actions": self.list_actions(plan_id=plan_id, limit=500),
        }

    async def _detect_incident(
        self,
        *,
        plan: dict[str, Any],
        policy: dict[str, Any],
        step_id: str | None,
        incident_type: str,
        severity: str,
        title: str,
        message: str,
        details: dict[str, Any],
        requested_action: str,
    ) -> dict[str, Any]:
        dedupe_key = f"{plan['id']}:{step_id or '-'}:{incident_type}"
        now = utc_now()
        created = False
        reopened = False

        with self._session_factory() as session:
            row = session.scalar(
                select(ExecutionSupervisorIncidentModel).where(
                    ExecutionSupervisorIncidentModel.dedupe_key == dedupe_key
                )
            )
            if row is None:
                row = ExecutionSupervisorIncidentModel(
                    plan_id=plan["id"],
                    step_id=step_id,
                    workspace_id=plan.get("workspace_id"),
                    policy_id=policy.get("id"),
                    incident_type=incident_type,
                    severity=severity,
                    status="open",
                    dedupe_key=dedupe_key,
                    title=title,
                    message=message,
                    details_json=deepcopy(details),
                    occurrence_count=1,
                    detected_at=now,
                    last_seen_at=now,
                )
                session.add(row)
                session.flush()
                created = True
            else:
                row.occurrence_count += 1
                row.last_seen_at = now
                row.title = title
                row.message = message
                row.details_json = deepcopy(details)
                row.severity = severity
                row.policy_id = policy.get("id")
                row.updated_at = now
                if row.status in {"resolved", "dismissed"}:
                    row.status = "open"
                    row.resolved_at = None
                    row.resolved_by = None
                    row.resolution = None
                    reopened = True
                session.flush()
            result = self._serialize_incident(row)

        if created:
            self._incidents_created += 1

        await self._event_bus.publish(
            Event(
                event_type=(
                    "execution_plan.supervisor.incident.detected"
                    if created or reopened
                    else "execution_plan.supervisor.incident.updated"
                ),
                source="execution_supervisor",
                workspace_id=plan.get("workspace_id"),
                correlation_id=plan["id"],
                payload={
                    "incident_id": result["id"],
                    "plan_id": plan["id"],
                    "step_id": step_id,
                    "incident_type": incident_type,
                    "severity": severity,
                    "requested_action": requested_action,
                    "created": created,
                    "reopened": reopened,
                },
            )
        )

        if created or reopened:
            await self._apply_policy_action(
                incident=result,
                policy=policy,
                action=requested_action,
            )
        return result

    async def _apply_policy_action(
        self,
        *,
        incident: dict[str, Any],
        policy: dict[str, Any],
        action: str,
    ) -> None:
        action_type = SupervisorActionType(action)
        if (
            action_type not in {
                SupervisorActionType.OBSERVE,
                SupervisorActionType.NOTIFY,
            }
            and not policy["automatic_actions_enabled"]
        ):
            action_type = SupervisorActionType.OBSERVE

        request = SupervisorInterventionRequest(
            action=action_type,
            reason=(
                f"Supervisor policy {policy['scope_key']} reacted to "
                f"incident {incident['incident_type']}."
            ),
            actor_id="execution-supervisor",
            incident_id=incident["id"],
            auto_start_replanned=policy["auto_start_replanned"],
            wait=False,
            idempotency_key=f"incident:{incident['id']}:{action_type.value}",
            metadata={
                "policy_id": policy["id"],
                "automatic_actions_enabled": policy[
                    "automatic_actions_enabled"
                ],
            },
        )
        await self.intervene(
            incident["plan_id"],
            request,
            automatic=True,
            policy_id=policy["id"],
        )

    def _schedule_action(self, action_id: str) -> None:
        task = asyncio.create_task(
            self._execute_action(action_id),
            name=f"execution-supervisor-action-{action_id}",
        )
        self._action_tasks.add(task)
        task.add_done_callback(self._action_tasks.discard)

    async def _execute_action(self, action_id: str) -> None:
        action = self._mark_action_running(action_id)
        if action is None or action["status"] not in {"running", "pending"}:
            return

        action_type = SupervisorActionType(action["action"])
        plan_id = action["plan_id"]
        reason = action["reason"]

        try:
            plan = self._plan_snapshot(plan_id)
            if plan is None:
                raise ExecutionSupervisorError("Execution Plan not found.")

            if action_type in {
                SupervisorActionType.OBSERVE,
                SupervisorActionType.NOTIFY,
            }:
                result: dict[str, Any] = {
                    "observed": True,
                    "notified": action_type == SupervisorActionType.NOTIFY,
                    "plan_status": plan["status"],
                }
            elif action_type == SupervisorActionType.CANCEL:
                if plan["status"] in self.TERMINAL_PLAN_STATUSES:
                    self._finish_action(
                        action_id,
                        status="skipped",
                        result={
                            "reason": "Plan already terminal.",
                            "plan_status": plan["status"],
                        },
                    )
                    return
                result = {
                    "runtime": await self._runtime.cancel(plan_id, reason),
                }
            elif action_type == SupervisorActionType.REPLAN:
                if plan["status"] == ExecutionPlanStatus.COMPLETED.value:
                    self._finish_action(
                        action_id,
                        status="skipped",
                        result={
                            "reason": "Completed Plan is not replanned.",
                            "plan_status": plan["status"],
                        },
                    )
                    return
                if plan["status"] == ExecutionPlanStatus.SUPERSEDED.value:
                    self._finish_action(
                        action_id,
                        status="skipped",
                        result={
                            "reason": "Plan already superseded.",
                            "plan_status": plan["status"],
                        },
                    )
                    return
                if plan["status"] == ExecutionPlanStatus.RUNNING.value:
                    await self._runtime.cancel(
                        plan_id,
                        f"Supervisor cancelled Plan before replanning: {reason}",
                    )
                auto_start = bool(
                    action["request"].get("auto_start_replanned", True)
                )
                replanned = await self._planner.replan(
                    plan_id,
                    ExecutionPlanReplanRequest(
                        reason=reason,
                        preserve_completed_outputs=True,
                        auto_validate=True,
                        auto_assign=True,
                        strict_assignment=False,
                        auto_start=auto_start,
                        wait=False,
                        force=True,
                        context={
                            "supervisor_action_id": action_id,
                            "incident_id": action.get("incident_id"),
                            "automatic": action["automatic"],
                        },
                    ),
                    automatic=False,
                )
                result = {"replan": replanned}
            else:  # pragma: no cover - enum keeps this unreachable.
                raise ExecutionSupervisorError(
                    f"Unsupported supervisor action {action_type.value}."
                )

            completed = self._finish_action(
                action_id,
                status="completed",
                result=result,
            )
            self._actions_completed += 1
            if completed and completed.get("incident_id") and action_type in {
                SupervisorActionType.CANCEL,
                SupervisorActionType.REPLAN,
            }:
                await self.resolve_incident(
                    completed["incident_id"],
                    actor_id="execution-supervisor",
                    reason=f"Supervisor action {action_type.value} completed.",
                )

            await self._event_bus.publish(
                Event(
                    event_type="execution_plan.supervisor.action.completed",
                    source="execution_supervisor",
                    workspace_id=action.get("workspace_id"),
                    correlation_id=plan_id,
                    payload={
                        "action_id": action_id,
                        "incident_id": action.get("incident_id"),
                        "plan_id": plan_id,
                        "action": action_type.value,
                    },
                )
            )
        except asyncio.CancelledError:
            self._finish_action(
                action_id,
                status="failed",
                error="Supervisor action cancelled during shutdown.",
            )
            raise
        except Exception as exc:
            self._actions_failed += 1
            self._finish_action(
                action_id,
                status="failed",
                error=str(exc),
            )
            await self._event_bus.publish(
                Event(
                    event_type="execution_plan.supervisor.action.failed",
                    source="execution_supervisor",
                    workspace_id=action.get("workspace_id"),
                    correlation_id=plan_id,
                    payload={
                        "action_id": action_id,
                        "incident_id": action.get("incident_id"),
                        "plan_id": plan_id,
                        "action": action_type.value,
                        "error_type": exc.__class__.__name__,
                        "message": str(exc),
                    },
                )
            )

    async def _monitor_loop(self) -> None:
        try:
            while self._running:
                try:
                    await self.scan_once()
                except Exception as exc:
                    await self._event_bus.publish(
                        Event(
                            event_type="execution_plan.supervisor.scan.failed",
                            source="execution_supervisor",
                            payload={
                                "error_type": exc.__class__.__name__,
                                "message": str(exc),
                            },
                        )
                    )
                await asyncio.sleep(self._minimum_check_interval())
        except asyncio.CancelledError:
            return

    def _minimum_check_interval(self) -> int:
        with self._session_factory() as session:
            values = list(
                session.scalars(
                    select(
                        ExecutionSupervisorPolicyModel.check_interval_seconds
                    ).where(ExecutionSupervisorPolicyModel.enabled.is_(True))
                ).all()
            )
        return max(1, min(values) if values else 5)

    def _ensure_default_policy(self) -> None:
        with self._session_factory() as session:
            existing = session.scalar(
                select(ExecutionSupervisorPolicyModel).where(
                    ExecutionSupervisorPolicyModel.scope_key == "global"
                )
            )
            if existing is not None:
                return
            session.add(
                ExecutionSupervisorPolicyModel(
                    scope_key="global",
                    workspace_id=None,
                    name="Global safe supervisor policy",
                    enabled=True,
                    automatic_actions_enabled=False,
                    check_interval_seconds=5,
                    stall_timeout_seconds=900,
                    max_step_runtime_seconds=600,
                    retry_warning_threshold=2,
                    failure_action="observe",
                    stall_action="observe",
                    long_running_action="observe",
                    retry_action="observe",
                    auto_start_replanned=True,
                    metadata_json={
                        "seeded": True,
                        "safety": "destructive automatic actions disabled",
                    },
                )
            )

    def _effective_policy_row(
        self,
        session: Session,
        workspace_id: str | None,
    ) -> ExecutionSupervisorPolicyModel | None:
        if workspace_id:
            row = session.scalar(
                select(ExecutionSupervisorPolicyModel).where(
                    ExecutionSupervisorPolicyModel.scope_key
                    == f"workspace:{workspace_id}"
                )
            )
            if row is not None:
                return row
        return session.scalar(
            select(ExecutionSupervisorPolicyModel).where(
                ExecutionSupervisorPolicyModel.scope_key == "global"
            )
        )

    @staticmethod
    def _policy_values(values: dict[str, Any]) -> dict[str, Any]:
        mapping = dict(values)
        if "metadata" in mapping:
            mapping["metadata_json"] = mapping.pop("metadata")
        for key in (
            "failure_action",
            "stall_action",
            "long_running_action",
            "retry_action",
        ):
            value = mapping.get(key)
            if isinstance(value, SupervisorActionType):
                mapping[key] = value.value
        return mapping

    def _plan_snapshot(self, plan_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(ExecutionPlanModel, plan_id)
            return None if row is None else self._serialize_plan(row)

    def _create_action_row(
        self,
        session: Session,
        *,
        plan: dict[str, Any],
        request: SupervisorInterventionRequest,
        automatic: bool,
        policy_id: str | None,
        dedupe_key: str | None,
    ) -> ExecutionSupervisorActionModel:
        row = ExecutionSupervisorActionModel(
            incident_id=request.incident_id,
            plan_id=plan["id"],
            step_id=None,
            workspace_id=plan.get("workspace_id"),
            policy_id=policy_id,
            action=request.action.value,
            status="pending",
            actor_id=request.actor_id,
            automatic=automatic,
            reason=request.reason,
            request_json=request.model_dump(mode="json"),
            result_json=None,
            error=None,
            dedupe_key=dedupe_key,
        )
        session.add(row)
        session.flush()
        return row

    def _mark_action_running(
        self,
        action_id: str,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(ExecutionSupervisorActionModel, action_id)
            if row is None:
                return None
            if row.status in {"completed", "failed", "skipped"}:
                return self._serialize_action(row)
            row.status = "running"
            row.started_at = utc_now()
            row.updated_at = utc_now()
            session.flush()
            return self._serialize_action(row)

    def _finish_action(
        self,
        action_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(ExecutionSupervisorActionModel, action_id)
            if row is None:
                return None
            row.status = status
            row.result_json = deepcopy(result)
            row.error = error
            row.finished_at = utc_now()
            row.updated_at = utc_now()
            session.flush()
            return self._serialize_action(row)

    @staticmethod
    def _serialize_plan(row: ExecutionPlanModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "source_task_id": row.source_task_id,
            "title": row.title,
            "status": row.status,
            "version": row.version,
            "metadata": deepcopy(row.metadata_json or {}),
            "started_at": iso(row.started_at),
            "updated_at": iso(row.updated_at),
            "finished_at": iso(row.finished_at),
            "started_at_raw": row.started_at,
            "updated_at_raw": row.updated_at,
        }

    @staticmethod
    def _serialize_step(row: ExecutionPlanStepModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "plan_id": row.plan_id,
            "step_key": row.step_key,
            "status": row.status,
            "started_at": iso(row.started_at),
            "updated_at": iso(row.updated_at),
            "finished_at": iso(row.finished_at),
            "started_at_raw": row.started_at,
            "updated_at_raw": row.updated_at,
            "finished_at_raw": row.finished_at,
        }

    @staticmethod
    def _serialize_policy(
        row: ExecutionSupervisorPolicyModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "scope_key": row.scope_key,
            "workspace_id": row.workspace_id,
            "name": row.name,
            "enabled": row.enabled,
            "automatic_actions_enabled": row.automatic_actions_enabled,
            "check_interval_seconds": row.check_interval_seconds,
            "stall_timeout_seconds": row.stall_timeout_seconds,
            "max_step_runtime_seconds": row.max_step_runtime_seconds,
            "retry_warning_threshold": row.retry_warning_threshold,
            "failure_action": row.failure_action,
            "stall_action": row.stall_action,
            "long_running_action": row.long_running_action,
            "retry_action": row.retry_action,
            "auto_start_replanned": row.auto_start_replanned,
            "metadata": deepcopy(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _serialize_incident(
        row: ExecutionSupervisorIncidentModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "plan_id": row.plan_id,
            "step_id": row.step_id,
            "workspace_id": row.workspace_id,
            "policy_id": row.policy_id,
            "incident_type": row.incident_type,
            "severity": row.severity,
            "status": row.status,
            "dedupe_key": row.dedupe_key,
            "title": row.title,
            "message": row.message,
            "details": deepcopy(row.details_json or {}),
            "occurrence_count": row.occurrence_count,
            "detected_at": iso(row.detected_at),
            "last_seen_at": iso(row.last_seen_at),
            "acknowledged_at": iso(row.acknowledged_at),
            "acknowledged_by": row.acknowledged_by,
            "resolved_at": iso(row.resolved_at),
            "resolved_by": row.resolved_by,
            "resolution": row.resolution,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _serialize_action(
        row: ExecutionSupervisorActionModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "incident_id": row.incident_id,
            "plan_id": row.plan_id,
            "step_id": row.step_id,
            "workspace_id": row.workspace_id,
            "policy_id": row.policy_id,
            "action": row.action,
            "status": row.status,
            "actor_id": row.actor_id,
            "automatic": row.automatic,
            "reason": row.reason,
            "request": deepcopy(row.request_json or {}),
            "result": deepcopy(row.result_json),
            "error": row.error,
            "dedupe_key": row.dedupe_key,
            "started_at": iso(row.started_at),
            "finished_at": iso(row.finished_at),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }
