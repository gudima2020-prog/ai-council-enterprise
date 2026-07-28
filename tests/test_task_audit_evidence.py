from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.events import Event
from backend.database.base import Base
from backend.database import models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401
from backend.task_engine.approval_schemas import TaskApprovalRequest
from backend.task_engine.approvals import ApprovalGateRuntime
from backend.task_engine.audit import TaskAuditManager
from backend.task_engine.repository import TaskRepository
from backend.task_engine.schemas import TaskCreate


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


@pytest.mark.asyncio
async def test_approval_evidence_contains_decision_and_fingerprint() -> None:
    scope = make_scope()

    with scope() as session:
        task = TaskRepository(session).create(
            TaskCreate(title="Approval evidence")
        )
        runtime = ApprovalGateRuntime(session)
        requested = runtime.request(
            task,
            TaskApprovalRequest(
                prompt="Approve execution",
                requested_by="worker-ai",
            ),
        )
        approval_id = requested["id"]
        task_id = task.id

        row = runtime.get(approval_id)
        assert row is not None
        row.status = "approved"
        row.decided_by = "user"
        row.decided_at = datetime.now(timezone.utc)
        row.decision_note = "Approved after review."

    manager = TaskAuditManager(session_factory=scope)
    await manager.record_event(
        Event(
            event_type="task.approval.requested",
            source="task_approval_manager",
            payload={
                "id": approval_id,
                "task_id": task_id,
                "requested_by": "worker-ai",
            },
        )
    )
    await manager.record_event(
        Event(
            event_type="task.approval.approved",
            source="task_approval_manager",
            payload={
                "id": approval_id,
                "task_id": task_id,
                "decided_by": "user",
                "decision_note": "Approved after review.",
            },
        )
    )

    evidence = manager.approval_evidence(approval_id)

    assert evidence is not None
    assert evidence["approval"]["status"] == "approved"
    assert evidence["approval"]["decided_by"] == "user"
    assert len(evidence["audit_events"]) == 2
    assert len(evidence["evidence_fingerprint_sha256"]) == 64
    assert evidence["chain_integrity"]["valid"] is True
