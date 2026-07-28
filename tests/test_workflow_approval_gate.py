from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.events import EventBus
from backend.database.base import Base
from backend.database import models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401
from backend.task_engine.approval_schemas import (
    ApprovalDecision,
    ApprovalDecisionRequest,
    ApprovalStatus,
)
from backend.task_engine.approvals import TaskApprovalManager
from backend.task_engine.enums import TaskStatus
from backend.task_engine.queue import TaskQueue
from backend.task_engine.repository import TaskRepository
from backend.task_engine.workflow import TaskWorkflowEngine
from backend.task_engine.workflow_templates import (
    WorkflowInstantiateRequest,
    WorkflowNodeDefinition,
    WorkflowTemplateCreate,
    WorkflowTemplateService,
)


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
async def test_workflow_node_waits_for_user_approval() -> None:
    scope = make_scope()
    queue = TaskQueue()
    event_bus = EventBus()

    with scope() as session:
        service = WorkflowTemplateService(session, event_bus)
        template = await service.create_template(
            WorkflowTemplateCreate(
                name="Approval workflow",
                root_node_key="execute",
                nodes=[
                    WorkflowNodeDefinition(
                        key="execute",
                        title="Execute confirmed action",
                        payload={
                            "action": "echo",
                            "value": "confirmed",
                        },
                        approval={
                            "required": True,
                            "gate_key": "user_confirmation",
                            "prompt": "Подтвердите запуск workflow.",
                        },
                    )
                ],
            )
        )
        instance = await service.instantiate(
            template["id"],
            WorkflowInstantiateRequest(
                creator="ai",
            ),
        )

        assert instance is not None
        root_task_id = instance["root_task_id"]

    workflow = TaskWorkflowEngine(
        queue=queue,
        event_bus=event_bus,
        session_factory=scope,
    )
    manager = TaskApprovalManager(
        event_bus=event_bus,
        workflow_engine=workflow,
        session_factory=scope,
    )

    started = await workflow.start_workflow(root_task_id)

    assert started is not None
    assert queue.qsize() == 0

    approvals = manager.list(
        status=ApprovalStatus.PENDING,
        task_id=root_task_id,
    )

    assert len(approvals) == 1

    with scope() as session:
        task = TaskRepository(session).get(root_task_id)

        assert task is not None
        assert task.status == TaskStatus.WAITING.value

    decision = await manager.decide(
        approval_id=approvals[0]["id"],
        request=ApprovalDecisionRequest(
            decision=ApprovalDecision.APPROVE,
            decided_by="user",
        ),
    )

    assert decision is not None
    assert decision["resume"]["enqueued"] is True
    assert queue.qsize() == 1
