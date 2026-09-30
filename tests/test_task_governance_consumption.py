from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.events import EventBus
from backend.database.base import Base
from backend.database import models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401
from backend.task_engine.enums import TaskStatus, TaskType
from backend.task_engine.executor import TaskExecutor
from backend.task_engine.governance import (
    GovernanceControl,
    TaskRiskClass,
)
from backend.task_engine.governance_consumption import (
    GOVERNANCE_CONSUMPTION_SCHEMA_VERSION,
    GOVERNANCE_VERIFICATION_STATE,
    TaskGovernanceConsumer,
)
from backend.task_engine.governance_materialization import (
    GOVERNANCE_PAYLOAD_KEY,
    GovernanceMaterializationSpec,
)
from backend.task_engine.parallel_executor import ParallelTaskExecutor
from backend.task_engine.queue import QueueItem, TaskQueue
from backend.task_engine.repository import TaskRepository
from backend.task_engine.schemas import TaskCreate
from backend.task_engine.state_machine import TaskStateMachine


def make_scope():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def scope():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return scope


def governance_spec(
    *,
    revision_id: str = "rev_consume_001",
    side_effects: tuple[str, ...] = (),
) -> GovernanceMaterializationSpec:
    return GovernanceMaterializationSpec(
        revision_id=revision_id,
        risk_class=TaskRiskClass.HIGH,
        execution_initiator="agent",
        side_effects=side_effects,
        requested_capabilities=(
            "shell.execute",
            "github.issue.comment",
        ),
        policy_tags=("runtime-consumption",),
    )


def queue_item(task_id: str) -> QueueItem:
    return QueueItem(
        priority_weight=30,
        sequence=0,
        task_id=task_id,
    )


def test_consumer_returns_none_for_plain_task() -> None:
    scope = make_scope()

    with scope() as session:
        task = TaskRepository(session).create(
            TaskCreate(
                title="Plain",
                payload={"action": "echo", "value": "plain"},
            )
        )

        consumed = TaskGovernanceConsumer().consume(task)

        assert consumed is None


def test_consumer_returns_immutable_canonical_requirements_only() -> None:
    scope = make_scope()

    with scope() as session:
        repository = TaskRepository(session)
        task = repository.create(
            TaskCreate(
                title="Governed",
                payload={"action": "echo", "value": "governed"},
            )
        )
        repository.materialize_governance(task, governance_spec())

        consumed = TaskGovernanceConsumer().consume(task)

        assert consumed is not None
        assert consumed.schema_version == (
            GOVERNANCE_CONSUMPTION_SCHEMA_VERSION
        )
        assert consumed.verification_state == (
            GOVERNANCE_VERIFICATION_STATE
        )
        assert consumed.authorization_state == "requirements_only"
        assert consumed.task_id == task.id
        assert consumed.revision_id == "rev_consume_001"
        assert consumed.requested_capabilities == (
            "github.issue.comment",
            "shell.execute",
        )
        assert (
            GovernanceControl.INDEPENDENT_REVIEW.value
            in consumed.controls
        )

        serialized = consumed.to_dict()
        assert "granted_capabilities" not in serialized
        assert "observed_evidence" not in serialized
        assert "approval" not in serialized
        assert "authorized" not in serialized
        assert "allowed" not in serialized

        with pytest.raises(FrozenInstanceError):
            consumed.task_id = "task_other"  # type: ignore[misc]


def test_human_gate_requirement_remains_requirement_only_metadata() -> None:
    scope = make_scope()

    with scope() as session:
        repository = TaskRepository(session)
        task = repository.create(
            TaskCreate(
                title="External write requirement",
                payload={"action": "echo"},
            )
        )
        repository.materialize_governance(
            task,
            governance_spec(side_effects=("external_write",)),
        )

        consumed = TaskGovernanceConsumer().consume(task)

        assert consumed is not None
        assert consumed.human_gate_required is True
        assert consumed.authorization_state == "requirements_only"
        serialized = consumed.to_dict()
        assert "human_approval" not in serialized
        assert "approval_status" not in serialized
        assert "authorized" not in serialized


@pytest.mark.asyncio
async def test_executor_passes_canonical_requirements_and_sanitizes_payload() -> None:
    scope = make_scope()
    queue = TaskQueue()
    seen: dict[str, object] = {}

    with scope() as session:
        repository = TaskRepository(session)
        task = repository.create(
            TaskCreate(
                task_type=TaskType.PLUGIN,
                title="Governed execution",
                payload={
                    "action": "custom",
                    "value": "business",
                },
            )
        )
        repository.materialize_governance(task, governance_spec())
        TaskStateMachine.transition(task, TaskStatus.QUEUED)
        task_id = task.id

    async def handler(context):
        seen["payload"] = context.payload
        seen["governance"] = context.governance
        return {"status": "handled"}

    executor = TaskExecutor(
        queue=queue,
        event_bus=EventBus(),
        session_factory=scope,
    )
    executor.register(TaskType.PLUGIN.value, handler)

    await executor.execute_queue_item(queue_item(task_id))

    payload = seen["payload"]
    assert isinstance(payload, dict)
    assert GOVERNANCE_PAYLOAD_KEY not in payload
    assert payload["value"] == "business"

    governance = seen["governance"]
    assert governance is not None
    assert governance.task_id == task_id
    assert governance.authorization_state == "requirements_only"

    with scope() as session:
        full = TaskRepository(session).get_full(task_id)
        assert full is not None
        assert full.status == TaskStatus.COMPLETED.value
        assert GOVERNANCE_PAYLOAD_KEY in full.payload_json
        assert len(full.runs) == 1
        metadata = full.runs[0].metadata_json
        assert metadata["governance_consumed"] is True
        assert metadata["governance_revision_id"] == (
            "rev_consume_001"
        )
        assert metadata["governance_authorization_state"] == (
            "requirements_only"
        )


@pytest.mark.asyncio
async def test_plain_executor_behavior_is_unchanged() -> None:
    scope = make_scope()
    seen: dict[str, object] = {}

    with scope() as session:
        repository = TaskRepository(session)
        task = repository.create(
            TaskCreate(
                task_type=TaskType.PLUGIN,
                title="Plain execution",
                payload={"value": "plain"},
            )
        )
        TaskStateMachine.transition(task, TaskStatus.QUEUED)
        task_id = task.id

    async def handler(context):
        seen["payload"] = context.payload
        seen["governance"] = context.governance
        return {"status": "handled"}

    executor = TaskExecutor(
        queue=TaskQueue(),
        event_bus=EventBus(),
        session_factory=scope,
    )
    executor.register(TaskType.PLUGIN.value, handler)

    await executor.execute_queue_item(queue_item(task_id))

    assert seen["payload"] == {"value": "plain"}
    assert seen["governance"] is None

    with scope() as session:
        full = TaskRepository(session).get_full(task_id)
        assert full is not None
        assert full.status == TaskStatus.COMPLETED.value
        assert len(full.runs) == 1
        assert "governance_consumed" not in (
            full.runs[0].metadata_json
        )


@pytest.mark.asyncio
async def test_tampered_governance_fails_before_handler_without_retry() -> None:
    scope = make_scope()
    called = False

    with scope() as session:
        repository = TaskRepository(session)
        task = repository.create(
            TaskCreate(
                task_type=TaskType.PLUGIN,
                title="Tampered execution",
                payload={"value": "must-not-run"},
                max_retries=3,
            )
        )
        repository.materialize_governance(task, governance_spec())

        payload = deepcopy(task.payload_json)
        payload[GOVERNANCE_PAYLOAD_KEY][
            "plan_fingerprint"
        ] = "0" * 64
        task.payload_json = payload
        TaskStateMachine.transition(task, TaskStatus.QUEUED)
        task_id = task.id

    async def handler(context):
        nonlocal called
        called = True
        return {"status": "unexpected"}

    executor = TaskExecutor(
        queue=TaskQueue(),
        event_bus=EventBus(),
        session_factory=scope,
    )
    executor.register(TaskType.PLUGIN.value, handler)

    await executor.execute_queue_item(queue_item(task_id))

    assert called is False
    stats = executor.stats()
    assert stats["governance_rejected"] == 1
    assert stats["failed"] == 1
    assert stats["pending_retries"] == 0

    with scope() as session:
        full = TaskRepository(session).get_full(task_id)
        assert full is not None
        assert full.status == TaskStatus.FAILED.value
        assert full.retry_count == 0
        assert len(full.runs) == 1
        run = full.runs[0]
        assert run.status == TaskStatus.FAILED.value
        assert run.metadata_json["governance_preflight"] is True
        assert run.metadata_json["retry_scheduled"] is False
        assert run.error == (
            "Materialized governance failed canonical "
            "recomputation."
        )
        assert any(
            log.metadata_json.get("governance_preflight") is True
            for log in full.logs
        )


@pytest.mark.asyncio
async def test_parallel_executor_inherits_governance_consumption_boundary() -> None:
    scope = make_scope()
    seen: dict[str, object] = {}

    with scope() as session:
        repository = TaskRepository(session)
        task = repository.create(
            TaskCreate(
                task_type=TaskType.PLUGIN,
                title="Parallel governed execution",
                payload={"value": "parallel"},
            )
        )
        repository.materialize_governance(task, governance_spec())
        TaskStateMachine.transition(task, TaskStatus.QUEUED)
        task_id = task.id

    async def handler(context):
        seen["payload"] = context.payload
        seen["governance"] = context.governance
        return {"status": "handled"}

    executor = ParallelTaskExecutor(
        queue=TaskQueue(),
        event_bus=EventBus(),
        session_factory=scope,
    )
    executor.register(TaskType.PLUGIN.value, handler)

    await executor.execute_queue_item(queue_item(task_id))

    payload = seen["payload"]
    assert isinstance(payload, dict)
    assert GOVERNANCE_PAYLOAD_KEY not in payload

    governance = seen["governance"]
    assert governance is not None
    assert governance.task_id == task_id
