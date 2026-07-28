from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.database.base import Base
from backend.database import models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401
from backend.task_engine.enums import TaskPriority, TaskStatus, TaskType
from backend.task_engine.repository import TaskRepository
from backend.task_engine.schemas import (
    TaskArtifactCreate,
    TaskCreate,
    TaskLogCreate,
    TaskRunCreate,
    TaskUpdate,
)
from backend.task_engine.state_machine import TaskStateMachine


def test_task_repository_crud_and_children() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        repository = TaskRepository(session)

        task = repository.create(
            TaskCreate(
                task_type=TaskType.SYSTEM,
                priority=TaskPriority.HIGH,
                title="Health check",
                payload={"check": "database"},
                max_retries=2,
            )
        )

        assert task.id.startswith("task_")
        assert repository.get(task.id) is not None

        # Status changes now belong exclusively to TaskStateMachine.
        TaskStateMachine.transition(task, TaskStatus.QUEUED)

        repository.update(
            task,
            TaskUpdate(
                executor="system_worker",
                description="Repository integration test",
            ),
        )

        run = repository.append_run(
            task.id,
            TaskRunCreate(
                status=TaskStatus.RUNNING,
                attempt=1,
            ),
        )

        repository.append_log(
            task.id,
            TaskLogCreate(
                run_id=run.id,
                message="Task started",
            ),
        )

        repository.append_artifact(
            task.id,
            TaskArtifactCreate(
                run_id=run.id,
                artifact_type="json",
                name="result.json",
                path="data/results/result.json",
                size_bytes=128,
            ),
        )

        session.commit()

        full = repository.get_full(task.id)

        assert full is not None
        assert full.status == TaskStatus.QUEUED.value
        assert full.executor == "system_worker"
        assert len(full.runs) == 1
        assert len(full.logs) == 1
        assert len(full.artifacts) == 1

        repository.delete(full)
        session.commit()

        assert repository.get(task.id) is None
