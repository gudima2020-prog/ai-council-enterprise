from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.events import Event
from backend.database.base import Base
from backend.database import models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401
from backend.task_engine.audit import (
    AuditImmutabilityError,
    TaskAuditManager,
)
from backend.task_engine.models import TaskAuditEventModel


def make_runtime():
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

    return engine, factory, scope


@pytest.mark.asyncio
async def test_audit_chain_is_sequential_and_valid() -> None:
    _, _, scope = make_runtime()
    manager = TaskAuditManager(session_factory=scope)

    await asyncio.gather(
        *[
            manager.record_event(
                Event(
                    event_type="task.updated",
                    source="test",
                    payload={"task_id": "task_1", "step": index},
                )
            )
            for index in range(10)
        ]
    )

    events = manager.list(task_id="task_1", limit=20)

    assert [item["sequence"] for item in events] == list(range(1, 11))
    assert manager.verify_chain()["valid"] is True


@pytest.mark.asyncio
async def test_tampering_is_detected() -> None:
    _, _, scope = make_runtime()
    manager = TaskAuditManager(session_factory=scope)

    await manager.record_event(
        Event(
            event_type="task.created",
            source="test",
            payload={"task_id": "task_1", "value": "original"},
        )
    )
    await manager.record_event(
        Event(
            event_type="task.completed",
            source="test",
            payload={"task_id": "task_1"},
        )
    )

    with scope() as session:
        session.execute(
            update(TaskAuditEventModel)
            .where(TaskAuditEventModel.sequence == 1)
            .values(payload_json={"task_id": "task_1", "value": "tampered"})
        )

    verification = manager.verify_chain()

    assert verification["valid"] is False
    assert any(
        error["type"] == "event_hash_mismatch"
        for error in verification["errors"]
    )


@pytest.mark.asyncio
async def test_orm_update_and_delete_are_blocked() -> None:
    _, factory, scope = make_runtime()
    manager = TaskAuditManager(session_factory=scope)

    created = await manager.record_event(
        Event(
            event_type="task.created",
            source="test",
            payload={"task_id": "task_1"},
        )
    )

    session = factory()
    try:
        row = session.get(TaskAuditEventModel, created["id"])
        assert row is not None
        row.source = "tampered"

        with pytest.raises(AuditImmutabilityError):
            session.flush()

        session.rollback()
        row = session.get(TaskAuditEventModel, created["id"])
        assert row is not None
        session.delete(row)

        with pytest.raises(AuditImmutabilityError):
            session.flush()
    finally:
        session.rollback()
        session.close()
