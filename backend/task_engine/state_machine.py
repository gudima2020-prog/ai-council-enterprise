from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from backend.task_engine.enums import TaskStatus
from backend.task_engine.models import TaskModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InvalidTaskTransition(ValueError):
    pass


@dataclass(frozen=True)
class TaskTransitionResult:
    previous_status: TaskStatus
    current_status: TaskStatus


class TaskStateMachine:
    TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
        TaskStatus.CREATED: {
            TaskStatus.QUEUED,
            TaskStatus.WAITING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.SKIPPED,
        },
        TaskStatus.QUEUED: {
            TaskStatus.PLANNING,
            TaskStatus.RUNNING,
            TaskStatus.WAITING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.SKIPPED,
        },
        TaskStatus.PLANNING: {
            TaskStatus.WAITING,
            TaskStatus.RUNNING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.SKIPPED,
        },
        TaskStatus.WAITING: {
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.SKIPPED,
        },
        TaskStatus.RUNNING: {
            TaskStatus.WAITING,
            TaskStatus.POST_PROCESSING,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.POST_PROCESSING: {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.FAILED: {
            TaskStatus.RETRYING,
            TaskStatus.CANCELLED,
        },
        TaskStatus.RETRYING: {
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.COMPLETED: set(),
        TaskStatus.CANCELLED: set(),
        TaskStatus.SKIPPED: set(),
    }

    TERMINAL_STATUSES = {
        TaskStatus.COMPLETED,
        TaskStatus.CANCELLED,
        TaskStatus.SKIPPED,
    }

    @classmethod
    def allowed_transitions(
        cls,
        current_status: TaskStatus | str,
    ) -> list[TaskStatus]:
        current = TaskStatus(current_status)
        return sorted(cls.TRANSITIONS[current], key=lambda item: item.value)

    @classmethod
    def can_transition(
        cls,
        current_status: TaskStatus | str,
        target_status: TaskStatus | str,
    ) -> bool:
        return TaskStatus(target_status) in cls.TRANSITIONS[TaskStatus(current_status)]

    @classmethod
    def transition(
        cls,
        task: TaskModel,
        target_status: TaskStatus | str,
    ) -> TaskTransitionResult:
        previous = TaskStatus(task.status)
        target = TaskStatus(target_status)

        if previous == target:
            raise InvalidTaskTransition(
                f"Task уже находится в статусе {target.value}."
            )
        if not cls.can_transition(previous, target):
            allowed = ", ".join(
                status.value for status in cls.allowed_transitions(previous)
            ) or "нет"
            raise InvalidTaskTransition(
                "Недопустимый переход Task: "
                f"{previous.value} -> {target.value}. "
                f"Допустимые переходы: {allowed}."
            )

        if target == TaskStatus.RETRYING:
            if task.retry_count >= task.max_retries:
                raise InvalidTaskTransition("Лимит повторных запусков исчерпан.")
            task.retry_count += 1
            task.started_at = None

        now = utc_now()
        task.status = target.value

        if target == TaskStatus.RUNNING and task.started_at is None:
            task.started_at = now

        if target in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.SKIPPED,
        }:
            task.finished_at = now
        else:
            task.finished_at = None

        return TaskTransitionResult(
            previous_status=previous,
            current_status=target,
        )
