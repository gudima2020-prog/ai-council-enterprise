from __future__ import annotations

from datetime import datetime, timezone

from backend.orchestration.enums import ExecutionPlanStatus
from backend.orchestration.models import ExecutionPlanModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InvalidExecutionPlanTransition(ValueError):
    pass


class ExecutionPlanStateMachine:
    TRANSITIONS: dict[ExecutionPlanStatus, set[ExecutionPlanStatus]] = {
        ExecutionPlanStatus.DRAFT: {
            ExecutionPlanStatus.VALIDATED,
            ExecutionPlanStatus.CANCELLED,
            ExecutionPlanStatus.SUPERSEDED,
        },
        ExecutionPlanStatus.VALIDATED: {
            ExecutionPlanStatus.DRAFT,
            ExecutionPlanStatus.READY,
            ExecutionPlanStatus.CANCELLED,
            ExecutionPlanStatus.SUPERSEDED,
        },
        ExecutionPlanStatus.READY: {
            ExecutionPlanStatus.DRAFT,
            ExecutionPlanStatus.RUNNING,
            ExecutionPlanStatus.CANCELLED,
            ExecutionPlanStatus.SUPERSEDED,
        },
        ExecutionPlanStatus.RUNNING: {
            ExecutionPlanStatus.COMPLETED,
            ExecutionPlanStatus.FAILED,
            ExecutionPlanStatus.CANCELLED,
        },
        ExecutionPlanStatus.FAILED: {
            ExecutionPlanStatus.DRAFT,
            ExecutionPlanStatus.READY,
            ExecutionPlanStatus.CANCELLED,
            ExecutionPlanStatus.SUPERSEDED,
        },
        ExecutionPlanStatus.COMPLETED: set(),
        ExecutionPlanStatus.CANCELLED: {
            ExecutionPlanStatus.SUPERSEDED,
        },
        ExecutionPlanStatus.SUPERSEDED: set(),
    }

    @classmethod
    def transition(
        cls,
        plan: ExecutionPlanModel,
        target: ExecutionPlanStatus | str,
    ) -> tuple[ExecutionPlanStatus, ExecutionPlanStatus]:
        current = ExecutionPlanStatus(plan.status)
        target_status = ExecutionPlanStatus(target)

        if current == target_status:
            raise InvalidExecutionPlanTransition(
                f"Execution Plan уже находится в статусе {target_status.value}."
            )
        if target_status not in cls.TRANSITIONS[current]:
            allowed = ", ".join(
                item.value for item in sorted(
                    cls.TRANSITIONS[current],
                    key=lambda value: value.value,
                )
            ) or "нет"
            raise InvalidExecutionPlanTransition(
                "Недопустимый переход Execution Plan: "
                f"{current.value} -> {target_status.value}. "
                f"Допустимые переходы: {allowed}."
            )

        now = utc_now()
        plan.status = target_status.value

        if target_status == ExecutionPlanStatus.RUNNING:
            plan.started_at = plan.started_at or now
        if target_status in {
            ExecutionPlanStatus.COMPLETED,
            ExecutionPlanStatus.FAILED,
            ExecutionPlanStatus.CANCELLED,
            ExecutionPlanStatus.SUPERSEDED,
        }:
            plan.finished_at = now
        else:
            plan.finished_at = None

        return current, target_status
