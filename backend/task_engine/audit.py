from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from contextlib import AbstractContextManager
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Callable

from sqlalchemy import event as sqlalchemy_event, func, select
from sqlalchemy.orm import Session

from backend.core.events import Event
from backend.database.session import session_scope
from backend.task_engine.models import (
    TaskApprovalModel,
    TaskAuditEventModel,
    TaskModel,
)
from backend.task_engine.repository import TaskRepository


SessionContextFactory = Callable[[], AbstractContextManager[Session]]
GENESIS_HASH = "0" * 64


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            timestamp = utc_now()
    else:
        timestamp = utc_now()

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def canonical_timestamp(value: Any) -> str:
    return coerce_datetime(value).isoformat(timespec="microseconds")


def normalize_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return normalize_json(value.value)
    if isinstance(value, (datetime, date)):
        if isinstance(value, datetime):
            return canonical_timestamp(value)
        return value.isoformat()
    if isinstance(value, dict):
        return {
            str(key): normalize_json(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [normalize_json(item) for item in value]
    if hasattr(value, "model_dump"):
        return normalize_json(value.model_dump())
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        normalize_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class AuditImmutabilityError(RuntimeError):
    pass


@sqlalchemy_event.listens_for(TaskAuditEventModel, "before_update")
def _block_audit_update(mapper, connection, target) -> None:
    raise AuditImmutabilityError(
        "Task audit records are immutable and cannot be updated."
    )


@sqlalchemy_event.listens_for(TaskAuditEventModel, "before_delete")
def _block_audit_delete(mapper, connection, target) -> None:
    raise AuditImmutabilityError(
        "Task audit records are immutable and cannot be deleted."
    )


class TaskAuditManager:
    """
    Single-process append-only audit writer with a SHA-256 hash chain.

    The asyncio lock serializes sequence allocation and hash generation inside
    one application process. Database-level uniqueness protects sequence and
    event identifiers from accidental duplicates.
    """

    def __init__(
        self,
        *,
        session_factory: SessionContextFactory = session_scope,
    ) -> None:
        self._session_factory = session_factory
        self._write_lock = asyncio.Lock()
        self._recorded = 0
        self._duplicates = 0

    def stats(self) -> dict[str, Any]:
        with self._session_factory() as session:
            count = session.scalar(
                select(func.count()).select_from(TaskAuditEventModel)
            ) or 0
            head = session.scalar(
                select(TaskAuditEventModel)
                .order_by(TaskAuditEventModel.sequence.desc())
                .limit(1)
            )
            head_sequence = head.sequence if head is not None else 0
            head_hash = head.event_hash if head is not None else GENESIS_HASH

        return {
            "stored_entries": int(count),
            "runtime_recorded": self._recorded,
            "runtime_duplicates": self._duplicates,
            "head_sequence": head_sequence,
            "head_hash": head_hash,
        }

    async def record_event(self, event: Event) -> dict[str, Any]:
        payload = normalize_json(getattr(event, "payload", {}) or {})
        event_type = str(getattr(event, "event_type", "unknown"))
        source = str(getattr(event, "source", "unknown"))
        event_id = str(
            getattr(event, "id", None) or f"event_{uuid.uuid4().hex}"
        )
        workspace_id = self._string_or_none(
            getattr(event, "workspace_id", None)
            or payload.get("workspace_id")
        )
        occurred_at = coerce_datetime(
            getattr(event, "occurred_at", None)
            or getattr(event, "timestamp", None)
            or getattr(event, "created_at", None)
            or utc_now()
        )

        task_id = self._extract_task_id(payload)
        approval_id = self._extract_approval_id(event_type, payload)
        workflow_instance_id = self._string_or_none(
            payload.get("workflow_instance_id")
            or payload.get("instance_id")
        )
        actor_type, actor_id = self._extract_actor(source, payload)

        return await self.append(
            event_id=event_id,
            event_type=event_type,
            source=source,
            workspace_id=workspace_id,
            task_id=task_id,
            approval_id=approval_id,
            workflow_instance_id=workflow_instance_id,
            actor_type=actor_type,
            actor_id=actor_id,
            payload=payload,
            occurred_at=occurred_at,
        )

    async def append(
        self,
        *,
        event_id: str,
        event_type: str,
        source: str,
        payload: dict[str, Any],
        occurred_at: datetime,
        workspace_id: str | None = None,
        task_id: str | None = None,
        approval_id: str | None = None,
        workflow_instance_id: str | None = None,
        actor_type: str = "system",
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        async with self._write_lock:
            with self._session_factory() as session:
                existing = session.scalar(
                    select(TaskAuditEventModel).where(
                        TaskAuditEventModel.event_id == event_id
                    )
                )
                if existing is not None:
                    self._duplicates += 1
                    return self.serialize(existing)

                previous = session.scalar(
                    select(TaskAuditEventModel)
                    .order_by(TaskAuditEventModel.sequence.desc())
                    .limit(1)
                )
                sequence = 1 if previous is None else previous.sequence + 1
                previous_hash = (
                    GENESIS_HASH if previous is None else previous.event_hash
                )
                recorded_at = utc_now()
                normalized_payload = normalize_json(payload)

                hash_material = self._hash_material(
                    sequence=sequence,
                    event_id=event_id,
                    event_type=event_type,
                    source=source,
                    workspace_id=workspace_id,
                    task_id=task_id,
                    approval_id=approval_id,
                    workflow_instance_id=workflow_instance_id,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    payload=normalized_payload,
                    occurred_at=occurred_at,
                    recorded_at=recorded_at,
                    previous_hash=previous_hash,
                )
                event_hash = hashlib.sha256(
                    canonical_json(hash_material).encode("utf-8")
                ).hexdigest()

                row = TaskAuditEventModel(
                    sequence=sequence,
                    event_id=event_id,
                    event_type=event_type,
                    source=source,
                    workspace_id=workspace_id,
                    task_id=task_id,
                    approval_id=approval_id,
                    workflow_instance_id=workflow_instance_id,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    payload_json=normalized_payload,
                    occurred_at=occurred_at,
                    recorded_at=recorded_at,
                    previous_hash=previous_hash,
                    event_hash=event_hash,
                )
                session.add(row)
                session.flush()
                result = self.serialize(row)

            self._recorded += 1
            return result

    def get(self, audit_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(TaskAuditEventModel, audit_id)
            return None if row is None else self.serialize(row)

    def list(
        self,
        *,
        workspace_id: str | None = None,
        task_id: str | None = None,
        approval_id: str | None = None,
        workflow_instance_id: str | None = None,
        event_type: str | None = None,
        actor_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(TaskAuditEventModel)

            if workspace_id is not None:
                statement = statement.where(
                    TaskAuditEventModel.workspace_id == workspace_id
                )
            if task_id is not None:
                statement = statement.where(
                    TaskAuditEventModel.task_id == task_id
                )
            if approval_id is not None:
                statement = statement.where(
                    TaskAuditEventModel.approval_id == approval_id
                )
            if workflow_instance_id is not None:
                statement = statement.where(
                    TaskAuditEventModel.workflow_instance_id
                    == workflow_instance_id
                )
            if event_type is not None:
                statement = statement.where(
                    TaskAuditEventModel.event_type == event_type
                )
            if actor_id is not None:
                statement = statement.where(
                    TaskAuditEventModel.actor_id == actor_id
                )

            statement = (
                statement
                .order_by(TaskAuditEventModel.sequence.asc())
                .offset(offset)
                .limit(limit)
            )
            rows = list(session.scalars(statement).all())

        return [self.serialize(row) for row in rows]

    def verify_chain(self) -> dict[str, Any]:
        with self._session_factory() as session:
            rows = list(
                session.scalars(
                    select(TaskAuditEventModel).order_by(
                        TaskAuditEventModel.sequence.asc()
                    )
                ).all()
            )

        errors: list[dict[str, Any]] = []
        expected_sequence = 1
        expected_previous_hash = GENESIS_HASH

        for row in rows:
            if row.sequence != expected_sequence:
                errors.append(
                    {
                        "sequence": row.sequence,
                        "type": "sequence_gap",
                        "expected": expected_sequence,
                    }
                )

            if row.previous_hash != expected_previous_hash:
                errors.append(
                    {
                        "sequence": row.sequence,
                        "type": "previous_hash_mismatch",
                        "expected": expected_previous_hash,
                        "actual": row.previous_hash,
                    }
                )

            expected_hash = hashlib.sha256(
                canonical_json(
                    self._hash_material(
                        sequence=row.sequence,
                        event_id=row.event_id,
                        event_type=row.event_type,
                        source=row.source,
                        workspace_id=row.workspace_id,
                        task_id=row.task_id,
                        approval_id=row.approval_id,
                        workflow_instance_id=row.workflow_instance_id,
                        actor_type=row.actor_type,
                        actor_id=row.actor_id,
                        payload=row.payload_json,
                        occurred_at=row.occurred_at,
                        recorded_at=row.recorded_at,
                        previous_hash=row.previous_hash,
                    )
                ).encode("utf-8")
            ).hexdigest()

            if row.event_hash != expected_hash:
                errors.append(
                    {
                        "sequence": row.sequence,
                        "type": "event_hash_mismatch",
                        "expected": expected_hash,
                        "actual": row.event_hash,
                    }
                )

            expected_sequence = row.sequence + 1
            expected_previous_hash = row.event_hash

        return {
            "valid": not errors,
            "entries": len(rows),
            "first_sequence": rows[0].sequence if rows else None,
            "last_sequence": rows[-1].sequence if rows else None,
            "head_hash": rows[-1].event_hash if rows else GENESIS_HASH,
            "errors": errors,
        }

    def task_trace(self, task_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            task = TaskRepository(session).get_full(task_id)
            approvals = list(
                session.scalars(
                    select(TaskApprovalModel)
                    .where(TaskApprovalModel.task_id == task_id)
                    .order_by(TaskApprovalModel.created_at.asc())
                ).all()
            )
            audit_rows = list(
                session.scalars(
                    select(TaskAuditEventModel)
                    .where(TaskAuditEventModel.task_id == task_id)
                    .order_by(TaskAuditEventModel.sequence.asc())
                ).all()
            )

            task_payload = self._serialize_task(task) if task is not None else None
            approval_payload = [
                self._serialize_approval(row) for row in approvals
            ]
            audit_payload = [self.serialize(row) for row in audit_rows]

        fingerprint = hashlib.sha256(
            canonical_json(
                {
                    "task_id": task_id,
                    "task": task_payload,
                    "approvals": approval_payload,
                    "audit_hashes": [
                        row["event_hash"] for row in audit_payload
                    ],
                }
            ).encode("utf-8")
        ).hexdigest()

        return {
            "task_id": task_id,
            "task_exists": task_payload is not None,
            "task": task_payload,
            "approvals": approval_payload,
            "audit_events": audit_payload,
            "trace_fingerprint_sha256": fingerprint,
            "chain_integrity": self.verify_chain(),
        }

    def approval_evidence(
        self,
        approval_id: str,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            approval = session.get(TaskApprovalModel, approval_id)

            if approval is None:
                return None

            task = session.get(TaskModel, approval.task_id)
            rows = list(
                session.scalars(
                    select(TaskAuditEventModel)
                    .where(
                        (TaskAuditEventModel.approval_id == approval_id)
                        | (
                            (TaskAuditEventModel.task_id == approval.task_id)
                            & TaskAuditEventModel.event_type.like(
                                "task.approval.%"
                            )
                        )
                    )
                    .order_by(TaskAuditEventModel.sequence.asc())
                ).all()
            )

            approval_payload = self._serialize_approval(approval)
            task_payload = self._serialize_task(task) if task is not None else None
            audit_payload = [self.serialize(row) for row in rows]
            task_id = approval.task_id

        evidence_core = {
            "approval": approval_payload,
            "task": task_payload,
            "audit_hashes": [row["event_hash"] for row in audit_payload],
        }
        evidence_hash = hashlib.sha256(
            canonical_json(evidence_core).encode("utf-8")
        ).hexdigest()

        return {
            "evidence_type": "task_approval_evidence",
            "approval_id": approval_id,
            "task_id": task_id,
            "approval": approval_payload,
            "task": task_payload,
            "audit_events": audit_payload,
            "evidence_fingerprint_sha256": evidence_hash,
            "chain_integrity": self.verify_chain(),
            "notice": (
                "This is a tamper-evident technical evidence package, "
                "not a qualified electronic signature."
            ),
        }

    @staticmethod
    def serialize(row: TaskAuditEventModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "sequence": row.sequence,
            "event_id": row.event_id,
            "event_type": row.event_type,
            "source": row.source,
            "workspace_id": row.workspace_id,
            "task_id": row.task_id,
            "approval_id": row.approval_id,
            "workflow_instance_id": row.workflow_instance_id,
            "actor_type": row.actor_type,
            "actor_id": row.actor_id,
            "payload": row.payload_json,
            "occurred_at": row.occurred_at,
            "recorded_at": row.recorded_at,
            "previous_hash": row.previous_hash,
            "event_hash": row.event_hash,
        }

    @staticmethod
    def _hash_material(
        *,
        sequence: int,
        event_id: str,
        event_type: str,
        source: str,
        workspace_id: str | None,
        task_id: str | None,
        approval_id: str | None,
        workflow_instance_id: str | None,
        actor_type: str,
        actor_id: str | None,
        payload: dict[str, Any],
        occurred_at: datetime,
        recorded_at: datetime,
        previous_hash: str,
    ) -> dict[str, Any]:
        return {
            "sequence": sequence,
            "event_id": event_id,
            "event_type": event_type,
            "source": source,
            "workspace_id": workspace_id,
            "task_id": task_id,
            "approval_id": approval_id,
            "workflow_instance_id": workflow_instance_id,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "payload": normalize_json(payload),
            "occurred_at": canonical_timestamp(occurred_at),
            "recorded_at": canonical_timestamp(recorded_at),
            "previous_hash": previous_hash,
        }

    @staticmethod
    def _extract_task_id(payload: dict[str, Any]) -> str | None:
        value = (
            payload.get("task_id")
            or payload.get("root_task_id")
            or payload.get("source_task_id")
        )
        return TaskAuditManager._string_or_none(value)

    @staticmethod
    def _extract_approval_id(
        event_type: str,
        payload: dict[str, Any],
    ) -> str | None:
        value = payload.get("approval_id")
        if value is None and event_type.startswith("task.approval."):
            value = payload.get("id")
        return TaskAuditManager._string_or_none(value)

    @staticmethod
    def _extract_actor(
        source: str,
        payload: dict[str, Any],
    ) -> tuple[str, str | None]:
        for key in (
            "decided_by",
            "requested_by",
            "actor_id",
            "user_id",
            "creator",
        ):
            value = payload.get(key)
            if value:
                return "user", str(value)

        return "system", source or None

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _serialize_approval(row: TaskApprovalModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "task_id": row.task_id,
            "workspace_id": row.workspace_id,
            "gate_key": row.gate_key,
            "status": row.status,
            "prompt": row.prompt,
            "requested_by": row.requested_by,
            "requested_at": row.requested_at,
            "decided_by": row.decided_by,
            "decided_at": row.decided_at,
            "decision_note": row.decision_note,
            "expires_at": row.expires_at,
            "metadata": row.metadata_json,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _serialize_task(task: TaskModel) -> dict[str, Any]:
        return {
            "id": task.id,
            "workspace_id": task.workspace_id,
            "task_type": task.task_type,
            "priority": task.priority,
            "status": task.status,
            "title": task.title,
            "description": task.description,
            "payload": task.payload_json,
            "result": task.result_json,
            "creator": task.creator,
            "executor": task.executor,
            "retry_count": task.retry_count,
            "max_retries": task.max_retries,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
            "runs": [
                {
                    "id": run.id,
                    "status": run.status,
                    "attempt": run.attempt,
                    "started_at": run.started_at,
                    "finished_at": run.finished_at,
                    "duration_ms": run.duration_ms,
                    "error": run.error,
                    "metadata": run.metadata_json,
                }
                for run in getattr(task, "runs", [])
            ],
            "logs": [
                {
                    "id": log.id,
                    "run_id": log.run_id,
                    "level": log.level,
                    "message": log.message,
                    "metadata": log.metadata_json,
                    "created_at": log.created_at,
                }
                for log in getattr(task, "logs", [])
            ],
            "artifacts": [
                {
                    "id": artifact.id,
                    "run_id": artifact.run_id,
                    "artifact_type": artifact.artifact_type,
                    "name": artifact.name,
                    "path": artifact.path,
                    "mime_type": artifact.mime_type,
                    "size_bytes": artifact.size_bytes,
                    "checksum": artifact.checksum,
                    "metadata": artifact.metadata_json,
                    "created_at": artifact.created_at,
                }
                for artifact in getattr(task, "artifacts", [])
            ],
        }
