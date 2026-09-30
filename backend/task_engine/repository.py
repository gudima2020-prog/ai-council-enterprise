from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.task_engine.enums import TaskStatus
from backend.task_engine.governance_materialization import (
    GOVERNANCE_PAYLOAD_KEY,
    GovernanceMaterializationError,
    GovernanceMaterializationSpec,
    TaskGovernanceMaterializer,
)
from backend.task_engine.models import (
    TaskArtifactModel,
    TaskLogModel,
    TaskModel,
    TaskRunModel,
)
from backend.task_engine.schemas import (
    TaskArtifactCreate,
    TaskCreate,
    TaskLogCreate,
    TaskRunCreate,
    TaskUpdate,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TaskRepository:
    def __init__(
        self,
        session: Session,
        *,
        governance_materializer: TaskGovernanceMaterializer | None = None,
    ) -> None:
        self.session = session
        self._governance_materializer = (
            governance_materializer or TaskGovernanceMaterializer()
        )

    def create(self, data: TaskCreate) -> TaskModel:
        payload = deepcopy(data.payload)
        if GOVERNANCE_PAYLOAD_KEY in payload:
            raise GovernanceMaterializationError(
                "The _governance payload key is reserved for canonical "
                "materialization."
            )

        row = TaskModel(
            workspace_id=data.workspace_id,
            task_type=data.task_type.value,
            priority=data.priority.value,
            title=data.title.strip(),
            description=data.description.strip(),
            payload_json=payload,
            creator=data.creator,
            executor=data.executor,
            max_retries=data.max_retries,
            retry_delay_seconds=data.retry_delay_seconds,
            timeout_seconds=data.timeout_seconds,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def materialize_governance(
        self,
        row: TaskModel,
        spec: GovernanceMaterializationSpec,
    ) -> dict[str, object]:
        envelope = self._governance_materializer.materialize(row, spec)
        self.session.flush()
        return envelope

    def validate_governance(
        self,
        row: TaskModel,
    ) -> dict[str, object] | None:
        if GOVERNANCE_PAYLOAD_KEY not in (row.payload_json or {}):
            return None
        return self._governance_materializer.validate(row)

    def recompute_governance_plan(self, row: TaskModel):
        return self._governance_materializer.recompute_plan(row)

    def get(self, task_id: str) -> TaskModel | None:
        return self.session.get(TaskModel, task_id)

    def get_run(self, run_id: str) -> TaskRunModel | None:
        return self.session.get(TaskRunModel, run_id)

    def get_full(self, task_id: str) -> TaskModel | None:
        statement = (
            select(TaskModel)
            .options(
                selectinload(TaskModel.runs),
                selectinload(TaskModel.logs),
                selectinload(TaskModel.artifacts),
            )
            .where(TaskModel.id == task_id)
        )
        return self.session.scalar(statement)

    def list(
        self,
        *,
        workspace_id: str | None = None,
        status: str | None = None,
        task_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TaskModel]:
        statement = select(TaskModel)

        if workspace_id is not None:
            statement = statement.where(TaskModel.workspace_id == workspace_id)

        if status is not None:
            statement = statement.where(TaskModel.status == status)

        if task_type is not None:
            statement = statement.where(TaskModel.task_type == task_type)

        statement = (
            statement
            .order_by(TaskModel.created_at.desc())
            .offset(offset)
            .limit(limit)
        )

        return list(self.session.scalars(statement).all())

    def list_by_statuses(
        self,
        statuses: list[str],
        *,
        limit: int = 1000,
    ) -> list[TaskModel]:
        if not statuses:
            return []

        statement = (
            select(TaskModel)
            .where(TaskModel.status.in_(statuses))
            .order_by(TaskModel.created_at.asc())
            .limit(limit)
        )
        return list(self.session.scalars(statement).all())

    def update(self, row: TaskModel, data: TaskUpdate) -> TaskModel:
        values = data.model_dump(exclude_unset=True)

        if "title" in values:
            row.title = values["title"].strip()
        if "description" in values:
            row.description = values["description"].strip()
        if "priority" in values:
            row.priority = values["priority"].value
        if "payload" in values:
            payload = deepcopy(values["payload"])
            existing_governance = (row.payload_json or {}).get(
                GOVERNANCE_PAYLOAD_KEY
            )
            incoming_governance = payload.get(GOVERNANCE_PAYLOAD_KEY)

            if existing_governance is None:
                if GOVERNANCE_PAYLOAD_KEY in payload:
                    raise GovernanceMaterializationError(
                        "The _governance payload key cannot be injected "
                        "through a generic Task update."
                    )
            else:
                if (
                    GOVERNANCE_PAYLOAD_KEY in payload
                    and incoming_governance != existing_governance
                ):
                    raise GovernanceMaterializationError(
                        "Materialized governance cannot be changed through "
                        "a generic Task update."
                    )
                payload[GOVERNANCE_PAYLOAD_KEY] = deepcopy(
                    existing_governance
                )

            row.payload_json = payload
        if "result" in values:
            row.result_json = values["result"]
        if "executor" in values:
            row.executor = values["executor"]
        if "max_retries" in values:
            row.max_retries = values["max_retries"]
        if "retry_delay_seconds" in values:
            row.retry_delay_seconds = values["retry_delay_seconds"]
        if "timeout_seconds" in values:
            row.timeout_seconds = values["timeout_seconds"]

        self.session.flush()
        return row

    def delete(self, row: TaskModel) -> None:
        self.session.delete(row)
        self.session.flush()

    def append_run(
        self,
        task_id: str,
        data: TaskRunCreate,
    ) -> TaskRunModel:
        row = TaskRunModel(
            task_id=task_id,
            status=data.status.value,
            attempt=data.attempt,
            started_at=data.started_at,
            finished_at=data.finished_at,
            duration_ms=data.duration_ms,
            error=data.error,
            metadata_json=data.metadata,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def finish_run(
        self,
        row: TaskRunModel,
        *,
        status: TaskStatus,
        duration_ms: float,
        error: str | None = None,
    ) -> TaskRunModel:
        row.status = status.value
        row.finished_at = utc_now()
        row.duration_ms = duration_ms
        row.error = error
        self.session.flush()
        return row

    def append_log(
        self,
        task_id: str,
        data: TaskLogCreate,
    ) -> TaskLogModel:
        row = TaskLogModel(
            task_id=task_id,
            run_id=data.run_id,
            level=data.level.upper(),
            message=data.message,
            metadata_json=data.metadata,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def append_artifact(
        self,
        task_id: str,
        data: TaskArtifactCreate,
    ) -> TaskArtifactModel:
        row = TaskArtifactModel(
            task_id=task_id,
            run_id=data.run_id,
            artifact_type=data.artifact_type,
            name=data.name,
            path=data.path,
            mime_type=data.mime_type,
            size_bytes=data.size_bytes,
            checksum=data.checksum,
            metadata_json=data.metadata,
        )
        self.session.add(row)
        self.session.flush()
        return row
