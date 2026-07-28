from __future__ import annotations

import pytest

from backend.task_engine.enums import TaskStatus
from backend.task_engine.models import TaskModel
from backend.task_engine.state_machine import (
    InvalidTaskTransition,
    TaskStateMachine,
)


def make_task(
    status: TaskStatus = TaskStatus.CREATED,
    max_retries: int = 2,
) -> TaskModel:
    return TaskModel(
        title="Test",
        status=status.value,
        max_retries=max_retries,
        retry_count=0,
    )


def test_created_can_move_to_queued() -> None:
    task = make_task()

    result = TaskStateMachine.transition(
        task,
        TaskStatus.QUEUED,
    )

    assert result.previous_status == TaskStatus.CREATED
    assert result.current_status == TaskStatus.QUEUED
    assert task.status == TaskStatus.QUEUED.value


def test_invalid_transition_is_rejected() -> None:
    task = make_task()

    with pytest.raises(InvalidTaskTransition):
        TaskStateMachine.transition(
            task,
            TaskStatus.COMPLETED,
        )


def test_running_sets_started_at() -> None:
    task = make_task(TaskStatus.QUEUED)

    TaskStateMachine.transition(
        task,
        TaskStatus.RUNNING,
    )

    assert task.started_at is not None


def test_completed_sets_finished_at() -> None:
    task = make_task(TaskStatus.RUNNING)

    TaskStateMachine.transition(
        task,
        TaskStatus.COMPLETED,
    )

    assert task.finished_at is not None


def test_retry_increments_counter() -> None:
    task = make_task(TaskStatus.FAILED, max_retries=2)

    TaskStateMachine.transition(
        task,
        TaskStatus.RETRYING,
    )

    assert task.retry_count == 1


def test_retry_limit_is_enforced() -> None:
    task = make_task(TaskStatus.FAILED, max_retries=1)
    task.retry_count = 1

    with pytest.raises(InvalidTaskTransition):
        TaskStateMachine.transition(
            task,
            TaskStatus.RETRYING,
        )


def test_terminal_task_has_no_transitions() -> None:
    assert TaskStateMachine.allowed_transitions(
        TaskStatus.COMPLETED
    ) == []
