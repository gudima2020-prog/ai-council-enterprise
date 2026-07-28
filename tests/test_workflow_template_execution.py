from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.events import EventBus
from backend.database.base import Base
from backend.database import models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401
from backend.task_engine.dependencies import TaskDependencyType
from backend.task_engine.enums import TaskStatus, TaskType
from backend.task_engine.parallel_executor import ParallelTaskExecutor
from backend.task_engine.queue import TaskQueue
from backend.task_engine.scheduler import TaskScheduler
from backend.task_engine.workflow import TaskWorkflowEngine
from backend.task_engine.workflow_templates import (
    WorkflowEdgeDefinition,
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
async def test_template_condition_and_result_mapping() -> None:
    scope = make_scope()
    event_bus = EventBus()
    queue = TaskQueue()

    with scope() as session:
        service = WorkflowTemplateService(session, event_bus)
        template = await service.create_template(
            WorkflowTemplateCreate(
                name="Conditional report",
                root_node_key="final",
                input_schema={"required": ["approved"]},
                nodes=[
                    WorkflowNodeDefinition(
                        key="source",
                        task_type=TaskType.SYSTEM,
                        title="Source",
                        payload={"action": "echo"},
                        result_mapping={"value": "input.approved"},
                    ),
                    WorkflowNodeDefinition(
                        key="approved_branch",
                        task_type=TaskType.SYSTEM,
                        title="Approved branch",
                        payload={"action": "echo"},
                        condition={
                            "path": "nodes.source.result.echo",
                            "operator": "eq",
                            "value": True,
                        },
                        result_mapping={
                            "value": "nodes.source.result.echo"
                        },
                    ),
                    WorkflowNodeDefinition(
                        key="rejected_branch",
                        task_type=TaskType.SYSTEM,
                        title="Rejected branch",
                        payload={"action": "echo", "value": "rejected"},
                        condition={
                            "path": "nodes.source.result.echo",
                            "operator": "eq",
                            "value": False,
                        },
                    ),
                    WorkflowNodeDefinition(
                        key="final",
                        task_type=TaskType.SYSTEM,
                        title="Final",
                        payload={"action": "echo", "value": "done"},
                    ),
                ],
                edges=[
                    WorkflowEdgeDefinition(from_node="source", to_node="approved_branch"),
                    WorkflowEdgeDefinition(from_node="source", to_node="rejected_branch"),
                    WorkflowEdgeDefinition(
                        from_node="approved_branch",
                        to_node="final",
                        dependency_type=TaskDependencyType.SOFT,
                    ),
                    WorkflowEdgeDefinition(
                        from_node="rejected_branch",
                        to_node="final",
                        dependency_type=TaskDependencyType.SOFT,
                    ),
                ],
            )
        )
        instance = await service.instantiate(
            template["id"],
            WorkflowInstantiateRequest(input={"approved": True}),
        )
        assert instance is not None
        instance_id = instance["id"]
        root_task_id = instance["root_task_id"]
        task_map = instance["node_task_map"]

    workflow = TaskWorkflowEngine(
        queue=queue,
        event_bus=event_bus,
        session_factory=scope,
    )
    executor = ParallelTaskExecutor(
        queue=queue,
        event_bus=event_bus,
        session_factory=scope,
        workflow_engine=workflow,
    )
    scheduler = TaskScheduler(
        queue=queue,
        event_bus=event_bus,
        handler=executor.execute_queue_item,
        worker_count=3,
    )

    await scheduler.start()
    result = await workflow.start_workflow(root_task_id)
    assert result is not None
    await asyncio.wait_for(queue.join(), timeout=3)
    await executor.shutdown()
    await scheduler.stop()

    with scope() as session:
        service = WorkflowTemplateService(session, event_bus)
        refreshed = service.refresh_instance(instance_id)
        assert refreshed is not None
        assert refreshed["status"] == "completed"
        assert refreshed["tasks"]["source"]["status"] == TaskStatus.COMPLETED.value
        assert refreshed["tasks"]["approved_branch"]["status"] == TaskStatus.COMPLETED.value
        assert refreshed["tasks"]["rejected_branch"]["status"] == TaskStatus.SKIPPED.value
        assert refreshed["tasks"]["final"]["status"] == TaskStatus.COMPLETED.value
        approved_task = session.get(task_models.TaskModel, task_map["approved_branch"])
        assert approved_task is not None
        assert approved_task.result_json["echo"] is True
