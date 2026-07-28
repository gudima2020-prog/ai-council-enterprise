from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.events import EventBus
from backend.database.base import Base
from backend.database import models as database_models  # noqa: F401
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.orchestration.collaboration import AgentCollaborationManager
from backend.orchestration.enums import ExecutionStepType
from backend.orchestration.models import AgentProfileModel
from backend.orchestration.repository import ExecutionPlanRepository
from backend.orchestration.runtime import (
    ExecutionPlanRuntime,
    StepExecutionContext,
)
from backend.orchestration.runtime_schemas import ExecutionPlanRunRequest
from backend.orchestration.schemas import (
    ExecutionPlanCreate,
    ExecutionPlanStepCreate,
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
async def test_runtime_supplies_shared_context_and_processes_directives() -> None:
    scope = make_scope()
    event_bus = EventBus()

    with scope() as session:
        agent = AgentProfileModel(
            agent_key="test.collaborator",
            display_name="Collaborator",
            roles_json=["analyst"],
            tools_json=[],
            executor_ref="test.collaborator",
        )
        session.add(agent)
        session.flush()

        repository = ExecutionPlanRepository(session)
        plan = repository.create(
            ExecutionPlanCreate(
                title="Runtime collaboration",
                objective="Verify shared context",
                steps=[
                    ExecutionPlanStepCreate(
                        step_key="analyze",
                        step_type=ExecutionStepType.AGENT,
                        title="Analyze",
                        agent_role="analyst",
                        input={"request": "analyze"},
                    )
                ],
            )
        )
        step = repository.list_steps(plan.id)[0]
        step.assigned_agent_id = agent.id
        step.assignment_json = {"manual": True}
        plan_id = plan.id
        agent_id = agent.id

    collaboration = AgentCollaborationManager(
        event_bus=event_bus,
        session_factory=scope,
    )
    await collaboration.upsert_context(
        plan_id,
        "brief",
        __import__(
            "backend.orchestration.collaboration_schemas",
            fromlist=["ContextEntryUpsertRequest"],
        ).ContextEntryUpsertRequest(value={"topic": "AI"}),
    )

    runtime = ExecutionPlanRuntime(
        event_bus=event_bus,
        session_factory=scope,
        collaboration_manager=collaboration,
    )

    async def executor(context: StepExecutionContext):
        assert context.shared_context["brief"] == {"topic": "AI"}
        assert context.conversation_id is not None
        return {
            "summary": "done",
            "_context_write": {"analysis.score": 91},
            "_messages": [
                {
                    "recipient_agent_id": agent_id,
                    "subject": "Analysis completed",
                    "content": {"score": 91},
                }
            ],
        }

    runtime.registry.register_agent("test.collaborator", executor)

    result = await runtime.start(
        plan_id,
        ExecutionPlanRunRequest(
            auto_validate=True,
            auto_assign=False,
            wait=True,
        ),
    )

    assert result is not None
    assert result["status"] == "completed"

    snapshot = collaboration.context_snapshot(plan_id)
    assert snapshot is not None
    assert snapshot["values"]["analysis.score"] == 91
    assert snapshot["values"]["steps.analyze.output"]["summary"] == "done"

    conversations = collaboration.list_conversations(plan_id)
    assert conversations is not None
    conversation = collaboration.get_conversation(conversations[0]["id"])
    assert conversation is not None
    subjects = [message["subject"] for message in conversation["messages"]]
    assert "Step started: analyze" in subjects
    assert "Analysis completed" in subjects
    assert "Step completed: analyze" in subjects

    await runtime.shutdown()


@pytest.mark.asyncio
async def test_runtime_collaboration_avoids_file_sqlite_lock(tmp_path) -> None:
    database_path = tmp_path / "runtime_collaboration.db"
    engine = create_engine(
        f"sqlite+pysqlite:///{database_path.as_posix()}",
        future=True,
        connect_args={"check_same_thread": False},
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

    event_bus = EventBus()

    with scope() as session:
        agent = AgentProfileModel(
            agent_key="test.sqlite-agent",
            display_name="SQLite Agent",
            roles_json=["worker"],
            tools_json=[],
            executor_ref="builtin.worker",
        )
        session.add(agent)
        session.flush()
        repository = ExecutionPlanRepository(session)
        plan = repository.create(
            ExecutionPlanCreate(
                title="SQLite collaboration",
                objective="Verify independent sessions",
                steps=[
                    ExecutionPlanStepCreate(
                        step_key="work",
                        step_type=ExecutionStepType.AGENT,
                        title="Work",
                        agent_role="worker",
                    )
                ],
            )
        )
        step = repository.list_steps(plan.id)[0]
        step.assigned_agent_id = agent.id
        plan_id = plan.id

    collaboration = AgentCollaborationManager(
        event_bus=event_bus,
        session_factory=scope,
    )
    runtime = ExecutionPlanRuntime(
        event_bus=event_bus,
        session_factory=scope,
        collaboration_manager=collaboration,
    )

    result = await runtime.start(
        plan_id,
        ExecutionPlanRunRequest(
            auto_validate=True,
            auto_assign=False,
            wait=True,
        ),
    )

    assert result is not None
    assert result["status"] == "completed"
    await runtime.shutdown()
