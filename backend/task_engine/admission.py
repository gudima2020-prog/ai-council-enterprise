from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
from typing import Any, Callable

from sqlalchemy import case, event as sa_event, func, select
from sqlalchemy.orm import Session

from backend.core.events import Event, EventBus
from backend.database.session import session_scope
from backend.task_engine.budget_schemas import (
    BudgetEnforcementMode,
    BudgetPeriod,
    BudgetPolicyCreate,
    BudgetPolicyUpdate,
)
from backend.task_engine.enums import TaskStatus
from backend.task_engine.models import (
    TaskBudgetPolicyModel,
    TaskCostLedgerModel,
    TaskModel,
)
from backend.task_engine.repository import TaskRepository
from backend.task_engine.schemas import TaskLogCreate
from backend.task_engine.state_machine import TaskStateMachine


SessionContextFactory = Callable[[], AbstractContextManager[Session]]
MONEY_QUANTUM = Decimal("0.000001")


class CostEntryType(StrEnum):
    ADMISSION = "admission"
    RESERVATION = "reservation"
    RELEASE = "release"
    CHARGE = "charge"
    ADJUSTMENT = "adjustment"
    OVERRIDE = "override"


class AdmissionError(ValueError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def money(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def money_float(value: Any) -> float:
    return float(money(value))


@sa_event.listens_for(TaskCostLedgerModel, "before_update")
def _prevent_cost_update(mapper, connection, target) -> None:  # noqa: ANN001
    raise RuntimeError("Task cost ledger entries are append-only.")


@sa_event.listens_for(TaskCostLedgerModel, "before_delete")
def _prevent_cost_delete(mapper, connection, target) -> None:  # noqa: ANN001
    raise RuntimeError("Task cost ledger entries cannot be deleted.")


class TaskAdmissionManager:
    """
    Central admission controller for task quotas and USD budgets.

    The manager is the final gate immediately before TaskExecutor claims a
    queued task. An asyncio lock makes evaluation plus reservation atomic
    inside the current application process.
    """

    ACTIVE_TASK_STATUSES = {
        TaskStatus.PLANNING.value,
        TaskStatus.RUNNING.value,
        TaskStatus.POST_PROCESSING.value,
    }
    QUEUED_TASK_STATUSES = {
        TaskStatus.QUEUED.value,
        TaskStatus.RETRYING.value,
    }

    def __init__(
        self,
        *,
        event_bus: EventBus,
        session_factory: SessionContextFactory = session_scope,
    ) -> None:
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._lock = asyncio.Lock()
        self._allowed = 0
        self._denied = 0
        self._observed = 0
        self._overridden = 0
        self._settled = 0

    def stats(self) -> dict[str, int]:
        return {
            "allowed": self._allowed,
            "denied": self._denied,
            "observed": self._observed,
            "overridden": self._overridden,
            "settled": self._settled,
        }

    async def create_policy(
        self,
        request: BudgetPolicyCreate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            self._disable_scope_policies(
                session,
                request.workspace_id,
                enabled=request.enabled,
            )
            row = TaskBudgetPolicyModel(
                workspace_id=request.workspace_id,
                name=request.name.strip(),
                enabled=request.enabled,
                period=request.period.value,
                enforcement_mode=request.enforcement_mode.value,
                limit_usd=(
                    money(request.limit_usd)
                    if request.limit_usd is not None
                    else None
                ),
                warning_threshold_percent=request.warning_threshold_percent,
                max_task_cost_usd=(
                    money(request.max_task_cost_usd)
                    if request.max_task_cost_usd is not None
                    else None
                ),
                max_tasks_per_period=request.max_tasks_per_period,
                max_queued=request.max_queued,
                max_running=request.max_running,
                allowed_task_types_json=request.allowed_task_types,
                denied_task_types_json=request.denied_task_types,
                metadata_json=request.metadata,
            )
            session.add(row)
            session.flush()
            payload = self._serialize_policy(row)

        await self._event_bus.publish(
            Event(
                event_type="task.budget.policy.created",
                source="task_admission_manager",
                workspace_id=payload["workspace_id"],
                payload={"policy_id": payload["id"], **payload},
            )
        )
        return payload

    def get_policy(self, policy_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(TaskBudgetPolicyModel, policy_id)
            return self._serialize_policy(row) if row is not None else None

    def list_policies(
        self,
        *,
        workspace_id: str | None = None,
        enabled: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(TaskBudgetPolicyModel)
            if workspace_id is not None:
                statement = statement.where(
                    TaskBudgetPolicyModel.workspace_id == workspace_id
                )
            if enabled is not None:
                statement = statement.where(
                    TaskBudgetPolicyModel.enabled == enabled
                )
            statement = (
                statement
                .order_by(TaskBudgetPolicyModel.updated_at.desc())
                .offset(offset)
                .limit(limit)
            )
            return [
                self._serialize_policy(row)
                for row in session.scalars(statement).all()
            ]

    async def update_policy(
        self,
        policy_id: str,
        request: BudgetPolicyUpdate,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(TaskBudgetPolicyModel, policy_id)
            if row is None:
                return None

            values = request.model_dump(exclude_unset=True)
            if values.get("enabled") is True:
                self._disable_scope_policies(
                    session,
                    row.workspace_id,
                    enabled=True,
                    exclude_id=row.id,
                )

            mapping = {
                "name": "name",
                "enabled": "enabled",
                "warning_threshold_percent": "warning_threshold_percent",
                "max_tasks_per_period": "max_tasks_per_period",
                "max_queued": "max_queued",
                "max_running": "max_running",
            }
            for source, target in mapping.items():
                if source in values:
                    setattr(row, target, values[source])

            if "period" in values:
                row.period = values["period"].value
            if "enforcement_mode" in values:
                row.enforcement_mode = values["enforcement_mode"].value
            if "limit_usd" in values:
                row.limit_usd = (
                    money(values["limit_usd"])
                    if values["limit_usd"] is not None
                    else None
                )
            if "max_task_cost_usd" in values:
                row.max_task_cost_usd = (
                    money(values["max_task_cost_usd"])
                    if values["max_task_cost_usd"] is not None
                    else None
                )
            if "allowed_task_types" in values:
                row.allowed_task_types_json = values["allowed_task_types"] or []
            if "denied_task_types" in values:
                row.denied_task_types_json = values["denied_task_types"] or []
            if "metadata" in values:
                row.metadata_json = values["metadata"] or {}

            overlap = set(row.allowed_task_types_json or []) & set(
                row.denied_task_types_json or []
            )
            if overlap:
                raise AdmissionError(
                    "Task type cannot be both allowed and denied: "
                    + ", ".join(sorted(overlap))
                )

            session.flush()
            payload = self._serialize_policy(row)

        await self._event_bus.publish(
            Event(
                event_type="task.budget.policy.updated",
                source="task_admission_manager",
                workspace_id=payload["workspace_id"],
                payload={"policy_id": payload["id"], **payload},
            )
        )
        return payload

    async def delete_policy(self, policy_id: str) -> bool:
        with self._session_factory() as session:
            row = session.get(TaskBudgetPolicyModel, policy_id)
            if row is None:
                return False
            workspace_id = row.workspace_id
            session.delete(row)

        await self._event_bus.publish(
            Event(
                event_type="task.budget.policy.deleted",
                source="task_admission_manager",
                workspace_id=workspace_id,
                payload={"policy_id": policy_id},
            )
        )
        return True

    async def evaluate_task(
        self,
        task_id: str,
        *,
        reserve: bool = False,
        actor_id: str | None = None,
        source: str = "runtime",
    ) -> dict[str, Any] | None:
        async with self._lock:
            with self._session_factory() as session:
                task = session.get(TaskModel, task_id)
                if task is None:
                    return None

                decision = self._evaluate_session(session, task)
                event_type = "task.admission.previewed"

                if reserve and decision["allowed"]:
                    self._record_admission(
                        session,
                        task,
                        decision,
                        actor_id=actor_id,
                        source=source,
                    )
                    event_type = "task.admission.allowed"
                    if decision["decision"] == "observed":
                        self._observed += 1
                    elif decision["decision"] == "overridden":
                        self._overridden += 1
                    else:
                        self._allowed += 1
                elif reserve and not decision["allowed"]:
                    self._mark_waiting(session, task, decision)
                    event_type = "task.admission.denied"
                    self._denied += 1

                decision["source"] = source
                decision["reserved"] = reserve and decision["allowed"]

            await self._event_bus.publish(
                Event(
                    event_type=event_type,
                    source="task_admission_manager",
                    workspace_id=decision["workspace_id"],
                    payload=decision,
                )
            )
            return decision

    async def override_task(
        self,
        task_id: str,
        *,
        actor_id: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            task = session.get(TaskModel, task_id)
            if task is None:
                return None

            policy = self._select_policy(session, task.workspace_id)
            row = self._append_entry(
                session,
                task_id=task.id,
                workspace_id=task.workspace_id,
                policy_id=policy.id if policy else None,
                entry_type=CostEntryType.OVERRIDE,
                amount_usd=Decimal("0"),
                actor_id=actor_id,
                reason=reason,
                metadata=metadata or {},
                reference_id=None,
            )
            TaskRepository(session).append_log(
                task.id,
                TaskLogCreate(
                    level="WARNING",
                    message=f"Task admission manually overridden: {reason}",
                    metadata={
                        "actor_id": actor_id,
                        "ledger_id": row.id,
                    },
                ),
            )
            payload = self._serialize_entry(row)

        await self._event_bus.publish(
            Event(
                event_type="task.admission.overridden",
                source="task_admission_manager",
                workspace_id=payload["workspace_id"],
                payload={
                    "task_id": task_id,
                    "actor_id": actor_id,
                    "reason": reason,
                    "ledger_id": payload["id"],
                },
            )
        )
        return payload

    async def handle_execution_event(self, event: Event) -> dict[str, Any] | None:
        task_id = event.payload.get("task_id")
        if not task_id:
            return None

        if event.event_type == "task.executor.completed":
            outcome = "completed"
        elif event.event_type in {
            "task.executor.failed",
            "task.executor.timed_out",
            "task.executor.governance_rejected",
            "task.cancelled",
        }:
            outcome = "released"
        else:
            return None

        return await self.settle_task(
            str(task_id),
            outcome=outcome,
            reference_id=event.id,
            event_payload=event.payload,
        )

    async def settle_task(
        self,
        task_id: str,
        *,
        outcome: str,
        reference_id: str,
        event_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        async with self._lock:
            with self._session_factory() as session:
                if self._reference_exists(session, f"settled:{reference_id}"):
                    return self.cost_status(task_id, session=session)

                task = session.get(TaskModel, task_id)
                workspace_id = task.workspace_id if task else None
                policy = self._select_policy(session, workspace_id)
                reserved = self._active_reservation(session, task_id)

                if reserved > 0:
                    self._append_entry(
                        session,
                        task_id=task_id,
                        workspace_id=workspace_id,
                        policy_id=policy.id if policy else None,
                        entry_type=CostEntryType.RELEASE,
                        amount_usd=-reserved,
                        actor_id="task_executor",
                        reason=f"Reservation released after {outcome}.",
                        metadata={"outcome": outcome},
                        reference_id=f"release:{reference_id}",
                    )

                actual = Decimal("0")
                if outcome == "completed":
                    actual = self._actual_cost(task, event_payload or {})
                    if actual <= 0 and task is not None:
                        actual = self._estimate_cost(task)

                if actual > 0:
                    self._append_entry(
                        session,
                        task_id=task_id,
                        workspace_id=workspace_id,
                        policy_id=policy.id if policy else None,
                        entry_type=CostEntryType.CHARGE,
                        amount_usd=actual,
                        actor_id="task_executor",
                        reason=f"Task cost settled after {outcome}.",
                        metadata={
                            "outcome": outcome,
                            "event_payload": event_payload or {},
                        },
                        reference_id=f"charge:{reference_id}",
                    )

                self._append_entry(
                    session,
                    task_id=task_id,
                    workspace_id=workspace_id,
                    policy_id=policy.id if policy else None,
                    entry_type=CostEntryType.ADJUSTMENT,
                    amount_usd=Decimal("0"),
                    actor_id="task_executor",
                    reason="Settlement marker.",
                    metadata={"outcome": outcome},
                    reference_id=f"settled:{reference_id}",
                )

                if task is not None:
                    TaskRepository(session).append_log(
                        task.id,
                        TaskLogCreate(
                            level="INFO",
                            message=(
                                "Task cost settled: "
                                f"reserved={money_float(reserved):.6f} USD, "
                                f"charged={money_float(actual):.6f} USD."
                            ),
                            metadata={
                                "reserved_usd": money_float(reserved),
                                "charged_usd": money_float(actual),
                                "outcome": outcome,
                            },
                        ),
                    )

                result = self._cost_status_session(session, task_id)
                self._settled += 1

            await self._event_bus.publish(
                Event(
                    event_type="task.cost.settled",
                    source="task_admission_manager",
                    workspace_id=result.get("workspace_id"),
                    payload={
                        "task_id": task_id,
                        "outcome": outcome,
                        "released_usd": money_float(reserved),
                        "charged_usd": money_float(actual),
                        "cost": result,
                    },
                )
            )
            return result

    async def manual_charge(
        self,
        task_id: str,
        *,
        amount_usd: float,
        actor_id: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        async with self._lock:
            with self._session_factory() as session:
                task = session.get(TaskModel, task_id)
                if task is None:
                    return None

                policy = self._select_policy(session, task.workspace_id)
                reserved = self._active_reservation(session, task.id)
                if reserved > 0:
                    self._append_entry(
                        session,
                        task_id=task.id,
                        workspace_id=task.workspace_id,
                        policy_id=policy.id if policy else None,
                        entry_type=CostEntryType.RELEASE,
                        amount_usd=-reserved,
                        actor_id=actor_id,
                        reason="Reservation released before manual charge.",
                        metadata=metadata or {},
                    )

                charge = self._append_entry(
                    session,
                    task_id=task.id,
                    workspace_id=task.workspace_id,
                    policy_id=policy.id if policy else None,
                    entry_type=CostEntryType.CHARGE,
                    amount_usd=money(amount_usd),
                    actor_id=actor_id,
                    reason=reason,
                    metadata=metadata or {},
                )
                result = self._cost_status_session(session, task.id)

            await self._event_bus.publish(
                Event(
                    event_type="task.cost.charged",
                    source="task_admission_manager",
                    workspace_id=result.get("workspace_id"),
                    payload={
                        "task_id": task_id,
                        "ledger_id": charge.id,
                        "amount_usd": money_float(charge.amount_usd),
                        "actor_id": actor_id,
                        "reason": reason,
                    },
                )
            )
            return result

    def cost_status(
        self,
        task_id: str,
        *,
        session: Session | None = None,
    ) -> dict[str, Any] | None:
        if session is not None:
            return self._cost_status_session(session, task_id)

        with self._session_factory() as own_session:
            return self._cost_status_session(own_session, task_id)

    def budget_status(
        self,
        *,
        workspace_id: str | None = None,
        policy_id: str | None = None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            policy = (
                session.get(TaskBudgetPolicyModel, policy_id)
                if policy_id is not None
                else self._select_policy(session, workspace_id)
            )
            usage = self._usage(session, policy, workspace_id)
            return {
                "workspace_id": workspace_id,
                "policy": self._serialize_policy(policy) if policy else None,
                "usage": usage,
            }

    def list_ledger(
        self,
        *,
        task_id: str | None = None,
        workspace_id: str | None = None,
        entry_type: CostEntryType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(TaskCostLedgerModel)
            if task_id is not None:
                statement = statement.where(
                    TaskCostLedgerModel.task_id == task_id
                )
            if workspace_id is not None:
                statement = statement.where(
                    TaskCostLedgerModel.workspace_id == workspace_id
                )
            if entry_type is not None:
                statement = statement.where(
                    TaskCostLedgerModel.entry_type == entry_type.value
                )
            statement = (
                statement
                .order_by(TaskCostLedgerModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            return [
                self._serialize_entry(row)
                for row in session.scalars(statement).all()
            ]

    def _evaluate_session(
        self,
        session: Session,
        task: TaskModel,
    ) -> dict[str, Any]:
        policy = self._select_policy(session, task.workspace_id)
        estimate = self._estimate_cost(task)
        override = self._has_override(session, task.id)
        usage = self._usage(session, policy, task.workspace_id)
        violations: list[str] = []
        warnings: list[str] = []

        if policy is not None:
            allowed_types = set(policy.allowed_task_types_json or [])
            denied_types = set(policy.denied_task_types_json or [])

            if allowed_types and task.task_type not in allowed_types:
                violations.append(
                    f"Task type {task.task_type} is not allowed by policy."
                )
            if task.task_type in denied_types:
                violations.append(
                    f"Task type {task.task_type} is denied by policy."
                )
            if (
                policy.max_task_cost_usd is not None
                and estimate > money(policy.max_task_cost_usd)
            ):
                violations.append(
                    "Estimated task cost exceeds max_task_cost_usd."
                )

            admitted_ids = set(usage["admitted_task_ids"])
            if (
                policy.max_tasks_per_period is not None
                and task.id not in admitted_ids
                and usage["tasks_admitted"] >= policy.max_tasks_per_period
            ):
                violations.append("Task admission quota for the period is exhausted.")

            if (
                policy.max_queued is not None
                and usage["queued"] > policy.max_queued
            ):
                violations.append("Queued Task quota is exceeded.")

            active_ids = set(usage["active_task_ids"])
            active_limit_reached = (
                len(active_ids) > policy.max_running
                if task.id in active_ids and policy.max_running is not None
                else (
                    policy.max_running is not None
                    and len(active_ids) >= policy.max_running
                )
            )
            if active_limit_reached:
                violations.append("Running/admitted Task quota is exhausted.")

            current_reservation = self._active_reservation(session, task.id)
            additional = Decimal("0") if current_reservation > 0 else estimate
            projected = (
                money(usage["spent_usd"])
                + money(usage["reserved_usd"])
                + additional
            )

            if policy.limit_usd is not None:
                limit_amount = money(policy.limit_usd)
                if projected > limit_amount:
                    violations.append("Budget limit would be exceeded.")
                elif limit_amount > 0:
                    percentage = float(projected / limit_amount * 100)
                    if percentage >= policy.warning_threshold_percent:
                        warnings.append(
                            "Budget warning threshold would be reached."
                        )
        else:
            projected = (
                money(usage["spent_usd"])
                + money(usage["reserved_usd"])
                + estimate
            )

        if override and violations:
            allowed = True
            decision = "overridden"
            warnings.extend(violations)
            violations = []
        elif policy is not None and (
            policy.enforcement_mode == BudgetEnforcementMode.OBSERVE.value
        ) and violations:
            allowed = True
            decision = "observed"
            warnings.extend(violations)
            violations = []
        else:
            allowed = not violations
            decision = "allowed" if allowed else "denied"

        return {
            "task_id": task.id,
            "workspace_id": task.workspace_id,
            "task_type": task.task_type,
            "task_status": task.status,
            "allowed": allowed,
            "decision": decision,
            "estimated_cost_usd": money_float(estimate),
            "projected_commitment_usd": money_float(projected),
            "violations": violations,
            "warnings": warnings,
            "override": override,
            "policy": self._serialize_policy(policy) if policy else None,
            "usage": {
                key: value
                for key, value in usage.items()
                if key not in {"active_task_ids", "admitted_task_ids"}
            },
        }

    def _record_admission(
        self,
        session: Session,
        task: TaskModel,
        decision: dict[str, Any],
        *,
        actor_id: str | None,
        source: str,
    ) -> None:
        policy_id = (
            decision["policy"]["id"]
            if decision.get("policy") is not None
            else None
        )
        attempt = task.retry_count + 1
        admission_reference = f"admission:{task.id}:{attempt}"

        if not self._reference_exists(session, admission_reference):
            self._append_entry(
                session,
                task_id=task.id,
                workspace_id=task.workspace_id,
                policy_id=policy_id,
                entry_type=CostEntryType.ADMISSION,
                amount_usd=Decimal("0"),
                actor_id=actor_id or "system",
                reason="Task admitted for execution.",
                metadata={
                    "source": source,
                    "decision": decision["decision"],
                    "attempt": attempt,
                    "warnings": decision["warnings"],
                },
                reference_id=admission_reference,
            )

        estimate = money(decision["estimated_cost_usd"])
        active_reservation = self._active_reservation(session, task.id)
        if estimate > 0 and active_reservation <= 0:
            self._append_entry(
                session,
                task_id=task.id,
                workspace_id=task.workspace_id,
                policy_id=policy_id,
                entry_type=CostEntryType.RESERVATION,
                amount_usd=estimate,
                actor_id=actor_id or "system",
                reason="Estimated task cost reserved.",
                metadata={"source": source, "attempt": attempt},
                reference_id=f"reservation:{task.id}:{attempt}",
            )

        TaskRepository(session).append_log(
            task.id,
            TaskLogCreate(
                level="WARNING" if decision["warnings"] else "INFO",
                message=(
                    "Task admitted by budget policy. "
                    f"Estimated cost: {money_float(estimate):.6f} USD."
                ),
                metadata={
                    "admission": True,
                    "decision": decision["decision"],
                    "policy_id": policy_id,
                    "warnings": decision["warnings"],
                },
            ),
        )

    def _mark_waiting(
        self,
        session: Session,
        task: TaskModel,
        decision: dict[str, Any],
    ) -> None:
        previous = task.status
        if task.status == TaskStatus.RETRYING.value:
            TaskStateMachine.transition(task, TaskStatus.QUEUED)
        if task.status in {
            TaskStatus.CREATED.value,
            TaskStatus.QUEUED.value,
            TaskStatus.PLANNING.value,
        }:
            TaskStateMachine.transition(task, TaskStatus.WAITING)

        TaskRepository(session).append_log(
            task.id,
            TaskLogCreate(
                level="WARNING",
                message="Task execution denied by budget/quota policy.",
                metadata={
                    "admission": True,
                    "previous_status": previous,
                    "violations": decision["violations"],
                    "policy_id": (
                        decision["policy"]["id"]
                        if decision.get("policy")
                        else None
                    ),
                },
            ),
        )

    def _usage(
        self,
        session: Session,
        policy: TaskBudgetPolicyModel | None,
        workspace_id: str | None,
    ) -> dict[str, Any]:
        start = self._period_start(policy.period if policy else "lifetime")
        ledger_filters = []
        task_filters = []

        if workspace_id is None:
            ledger_filters.append(TaskCostLedgerModel.workspace_id.is_(None))
            task_filters.append(TaskModel.workspace_id.is_(None))
        else:
            ledger_filters.append(TaskCostLedgerModel.workspace_id == workspace_id)
            task_filters.append(TaskModel.workspace_id == workspace_id)

        if start is not None:
            ledger_filters.append(TaskCostLedgerModel.created_at >= start)

        spent = session.scalar(
            select(func.coalesce(func.sum(TaskCostLedgerModel.amount_usd), 0))
            .where(
                *ledger_filters,
                TaskCostLedgerModel.entry_type.in_([
                    CostEntryType.CHARGE.value,
                    CostEntryType.ADJUSTMENT.value,
                ]),
            )
        )
        reserved = session.scalar(
            select(func.coalesce(func.sum(TaskCostLedgerModel.amount_usd), 0))
            .where(
                *ledger_filters,
                TaskCostLedgerModel.entry_type.in_([
                    CostEntryType.RESERVATION.value,
                    CostEntryType.RELEASE.value,
                ]),
            )
        )

        admitted_statement = select(TaskCostLedgerModel.task_id).where(
            *ledger_filters,
            TaskCostLedgerModel.entry_type == CostEntryType.ADMISSION.value,
            TaskCostLedgerModel.task_id.is_not(None),
        ).distinct()
        admitted_ids = {
            str(value)
            for value in session.scalars(admitted_statement).all()
            if value is not None
        }

        active_reservation_rows = session.execute(
            select(
                TaskCostLedgerModel.task_id,
                func.sum(TaskCostLedgerModel.amount_usd),
            )
            .where(
                *(
                    [TaskCostLedgerModel.workspace_id.is_(None)]
                    if workspace_id is None
                    else [TaskCostLedgerModel.workspace_id == workspace_id]
                ),
                TaskCostLedgerModel.entry_type.in_([
                    CostEntryType.RESERVATION.value,
                    CostEntryType.RELEASE.value,
                ]),
                TaskCostLedgerModel.task_id.is_not(None),
            )
            .group_by(TaskCostLedgerModel.task_id)
            .having(func.sum(TaskCostLedgerModel.amount_usd) > 0)
        ).all()
        active_ids = {str(row[0]) for row in active_reservation_rows}

        running_ids = {
            str(value)
            for value in session.scalars(
                select(TaskModel.id).where(
                    *task_filters,
                    TaskModel.status.in_(self.ACTIVE_TASK_STATUSES),
                )
            ).all()
        }
        active_ids |= running_ids

        queued = int(
            session.scalar(
                select(func.count(TaskModel.id)).where(
                    *task_filters,
                    TaskModel.status.in_(self.QUEUED_TASK_STATUSES),
                )
            )
            or 0
        )

        spent_amount = money(spent)
        reserved_amount = max(money(reserved), Decimal("0"))
        limit_amount = money(policy.limit_usd) if policy and policy.limit_usd is not None else None
        remaining = (
            max(limit_amount - spent_amount - reserved_amount, Decimal("0"))
            if limit_amount is not None
            else None
        )

        return {
            "period": policy.period if policy else "lifetime",
            "period_start": start,
            "spent_usd": money_float(spent_amount),
            "reserved_usd": money_float(reserved_amount),
            "committed_usd": money_float(spent_amount + reserved_amount),
            "limit_usd": money_float(limit_amount) if limit_amount is not None else None,
            "remaining_usd": money_float(remaining) if remaining is not None else None,
            "tasks_admitted": len(admitted_ids),
            "queued": queued,
            "active": len(active_ids),
            "active_task_ids": sorted(active_ids),
            "admitted_task_ids": sorted(admitted_ids),
        }

    def _cost_status_session(
        self,
        session: Session,
        task_id: str,
    ) -> dict[str, Any] | None:
        task = session.get(TaskModel, task_id)
        entries = list(
            session.scalars(
                select(TaskCostLedgerModel)
                .where(TaskCostLedgerModel.task_id == task_id)
                .order_by(TaskCostLedgerModel.created_at.asc())
            ).all()
        )

        if task is None and not entries:
            return None

        reserved = sum(
            (
                money(row.amount_usd)
                for row in entries
                if row.entry_type in {
                    CostEntryType.RESERVATION.value,
                    CostEntryType.RELEASE.value,
                }
            ),
            Decimal("0"),
        )
        spent = sum(
            (
                money(row.amount_usd)
                for row in entries
                if row.entry_type in {
                    CostEntryType.CHARGE.value,
                    CostEntryType.ADJUSTMENT.value,
                }
            ),
            Decimal("0"),
        )

        return {
            "task_id": task_id,
            "workspace_id": (
                task.workspace_id
                if task is not None
                else entries[-1].workspace_id
            ),
            "estimated_cost_usd": (
                money_float(self._estimate_cost(task))
                if task is not None
                else None
            ),
            "reserved_usd": money_float(max(reserved, Decimal("0"))),
            "spent_usd": money_float(spent),
            "override": any(
                row.entry_type == CostEntryType.OVERRIDE.value
                for row in entries
            ),
            "entries": [self._serialize_entry(row) for row in entries],
        }

    def _select_policy(
        self,
        session: Session,
        workspace_id: str | None,
    ) -> TaskBudgetPolicyModel | None:
        if workspace_id is not None:
            row = session.scalar(
                select(TaskBudgetPolicyModel)
                .where(
                    TaskBudgetPolicyModel.workspace_id == workspace_id,
                    TaskBudgetPolicyModel.enabled.is_(True),
                )
                .order_by(TaskBudgetPolicyModel.updated_at.desc())
            )
            if row is not None:
                return row

        return session.scalar(
            select(TaskBudgetPolicyModel)
            .where(
                TaskBudgetPolicyModel.workspace_id.is_(None),
                TaskBudgetPolicyModel.enabled.is_(True),
            )
            .order_by(TaskBudgetPolicyModel.updated_at.desc())
        )

    @staticmethod
    def _period_start(period: str) -> datetime | None:
        now = utc_now()
        if period == BudgetPeriod.DAILY.value:
            return datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        if period == BudgetPeriod.MONTHLY.value:
            return datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        return None

    @staticmethod
    def _estimate_cost(task: TaskModel) -> Decimal:
        payload = task.payload_json or {}
        cost = payload.get("_cost", {})

        if isinstance(cost, (int, float, str)):
            return max(money(cost), Decimal("0"))
        if not isinstance(cost, dict):
            cost = {}

        for value in (
            cost.get("estimated_usd"),
            payload.get("estimated_cost_usd"),
        ):
            if value is not None:
                return max(money(value), Decimal("0"))

        input_tokens = money(cost.get("input_tokens"))
        output_tokens = money(cost.get("output_tokens"))
        input_rate = money(cost.get("input_usd_per_million"))
        output_rate = money(cost.get("output_usd_per_million"))
        calculated = (
            input_tokens * input_rate / Decimal("1000000")
            + output_tokens * output_rate / Decimal("1000000")
        )
        return max(money(calculated), Decimal("0"))

    @classmethod
    def _actual_cost(
        cls,
        task: TaskModel | None,
        event_payload: dict[str, Any],
    ) -> Decimal:
        direct = event_payload.get("actual_cost_usd")
        if direct is not None:
            return max(money(direct), Decimal("0"))
        if task is None:
            return Decimal("0")

        result = task.result_json or {}
        if not isinstance(result, dict):
            return Decimal("0")

        usage = result.get("_usage", {})
        if isinstance(usage, dict) and usage.get("cost_usd") is not None:
            return max(money(usage["cost_usd"]), Decimal("0"))

        for key in ("_cost_usd", "cost_usd", "actual_cost_usd"):
            if result.get(key) is not None:
                return max(money(result[key]), Decimal("0"))

        return Decimal("0")

    def _active_reservation(
        self,
        session: Session,
        task_id: str,
    ) -> Decimal:
        value = session.scalar(
            select(func.coalesce(func.sum(TaskCostLedgerModel.amount_usd), 0))
            .where(
                TaskCostLedgerModel.task_id == task_id,
                TaskCostLedgerModel.entry_type.in_([
                    CostEntryType.RESERVATION.value,
                    CostEntryType.RELEASE.value,
                ]),
            )
        )
        return max(money(value), Decimal("0"))

    def _has_override(self, session: Session, task_id: str) -> bool:
        return bool(
            session.scalar(
                select(func.count(TaskCostLedgerModel.id)).where(
                    TaskCostLedgerModel.task_id == task_id,
                    TaskCostLedgerModel.entry_type
                    == CostEntryType.OVERRIDE.value,
                )
            )
        )

    @staticmethod
    def _reference_exists(session: Session, reference_id: str) -> bool:
        return bool(
            session.scalar(
                select(func.count(TaskCostLedgerModel.id)).where(
                    TaskCostLedgerModel.reference_id == reference_id
                )
            )
        )

    def _append_entry(
        self,
        session: Session,
        *,
        task_id: str | None,
        workspace_id: str | None,
        policy_id: str | None,
        entry_type: CostEntryType,
        amount_usd: Decimal,
        actor_id: str | None,
        reason: str | None,
        metadata: dict[str, Any],
        reference_id: str | None = None,
    ) -> TaskCostLedgerModel:
        if reference_id is not None:
            existing = session.scalar(
                select(TaskCostLedgerModel).where(
                    TaskCostLedgerModel.reference_id == reference_id
                )
            )
            if existing is not None:
                return existing

        row = TaskCostLedgerModel(
            reference_id=reference_id,
            task_id=task_id,
            workspace_id=workspace_id,
            policy_id=policy_id,
            entry_type=entry_type.value,
            amount_usd=money(amount_usd),
            actor_id=actor_id,
            reason=reason,
            metadata_json=metadata,
        )
        session.add(row)
        session.flush()
        return row

    @staticmethod
    def _disable_scope_policies(
        session: Session,
        workspace_id: str | None,
        *,
        enabled: bool,
        exclude_id: str | None = None,
    ) -> None:
        if not enabled:
            return

        statement = select(TaskBudgetPolicyModel).where(
            TaskBudgetPolicyModel.enabled.is_(True)
        )
        if workspace_id is None:
            statement = statement.where(
                TaskBudgetPolicyModel.workspace_id.is_(None)
            )
        else:
            statement = statement.where(
                TaskBudgetPolicyModel.workspace_id == workspace_id
            )
        if exclude_id is not None:
            statement = statement.where(
                TaskBudgetPolicyModel.id != exclude_id
            )

        for row in session.scalars(statement).all():
            row.enabled = False

    @staticmethod
    def _serialize_policy(
        row: TaskBudgetPolicyModel | None,
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "name": row.name,
            "enabled": row.enabled,
            "period": row.period,
            "enforcement_mode": row.enforcement_mode,
            "limit_usd": (
                money_float(row.limit_usd)
                if row.limit_usd is not None
                else None
            ),
            "warning_threshold_percent": row.warning_threshold_percent,
            "max_task_cost_usd": (
                money_float(row.max_task_cost_usd)
                if row.max_task_cost_usd is not None
                else None
            ),
            "max_tasks_per_period": row.max_tasks_per_period,
            "max_queued": row.max_queued,
            "max_running": row.max_running,
            "allowed_task_types": row.allowed_task_types_json,
            "denied_task_types": row.denied_task_types_json,
            "metadata": row.metadata_json,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _serialize_entry(row: TaskCostLedgerModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "reference_id": row.reference_id,
            "task_id": row.task_id,
            "workspace_id": row.workspace_id,
            "policy_id": row.policy_id,
            "entry_type": row.entry_type,
            "amount_usd": money_float(row.amount_usd),
            "actor_id": row.actor_id,
            "reason": row.reason,
            "metadata": row.metadata_json,
            "created_at": row.created_at,
        }
