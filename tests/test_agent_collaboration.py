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
from backend.orchestration.collaboration import (
    AgentCollaborationManager,
    ContextVersionConflict,
)
from backend.orchestration.collaboration_schemas import (
    AgentMessageCreateRequest,
    ContextEntryUpsertRequest,
    ContextScopeType,
    DelegationCreateRequest,
)
from backend.orchestration.models import AgentProfileModel
from backend.orchestration.repository import ExecutionPlanRepository
from backend.orchestration.schemas import ExecutionPlanCreate
from backend.orchestration.runtime import ExecutionPlanRuntime


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


def seed_plan_and_agents(scope):
    with scope() as session:
        first = AgentProfileModel(
            agent_key="test.first",
            display_name="First Agent",
            roles_json=["worker"],
            tools_json=[],
            executor_ref="builtin.worker",
        )
        second = AgentProfileModel(
            agent_key="test.second",
            display_name="Second Agent",
            roles_json=["worker"],
            tools_json=[],
            executor_ref="builtin.worker",
        )
        session.add_all([first, second])
        session.flush()
        plan = ExecutionPlanRepository(session).create(
            ExecutionPlanCreate(
                title="Collaboration plan",
                objective="Test collaboration",
            )
        )
        return plan.id, first.id, second.id


@pytest.mark.asyncio
async def test_messages_context_versions_and_scope_override() -> None:
    scope = make_scope()
    plan_id, first_id, second_id = seed_plan_and_agents(scope)
    manager = AgentCollaborationManager(
        event_bus=EventBus(),
        session_factory=scope,
    )

    conversation = manager.ensure_plan_conversation(plan_id)
    first_message = await manager.send_message(
        conversation["id"],
        AgentMessageCreateRequest(
            sender_agent_id=first_id,
            recipient_agent_id=second_id,
            subject="Request",
            content={"question": "ready?"},
        ),
    )
    second_message = await manager.send_message(
        conversation["id"],
        AgentMessageCreateRequest(
            sender_agent_id=second_id,
            recipient_agent_id=first_id,
            subject="Response",
            content={"answer": True},
            reply_to_message_id=first_message["id"],
        ),
    )

    assert first_message["sequence"] == 1
    assert second_message["sequence"] == 2

    handled = await manager.mark_message_read(
        first_message["id"],
        handled=True,
    )
    assert handled is not None
    assert handled["status"] == "handled"

    created = await manager.upsert_context(
        plan_id,
        "market.signal",
        ContextEntryUpsertRequest(value="hold"),
    )
    assert created["version"] == 1

    updated = await manager.upsert_context(
        plan_id,
        "market.signal",
        ContextEntryUpsertRequest(
            value="buy",
            expected_version=1,
        ),
    )
    assert updated["version"] == 2

    with pytest.raises(ContextVersionConflict):
        await manager.upsert_context(
            plan_id,
            "market.signal",
            ContextEntryUpsertRequest(
                value="sell",
                expected_version=1,
            ),
        )

    await manager.upsert_context(
        plan_id,
        "market.signal",
        ContextEntryUpsertRequest(
            value="agent-specific",
            scope_type=ContextScopeType.AGENT,
            scope_id=second_id,
            writer_agent_id=second_id,
        ),
    )

    snapshot = manager.context_snapshot(
        plan_id,
        agent_id=second_id,
    )
    assert snapshot is not None
    assert snapshot["values"]["market.signal"] == "agent-specific"


@pytest.mark.asyncio
async def test_delegation_executes_with_registered_agent() -> None:
    scope = make_scope()
    plan_id, first_id, second_id = seed_plan_and_agents(scope)
    event_bus = EventBus()
    manager = AgentCollaborationManager(
        event_bus=event_bus,
        session_factory=scope,
    )
    runtime = ExecutionPlanRuntime(
        event_bus=event_bus,
        session_factory=scope,
        collaboration_manager=manager,
    )

    delegation = await manager.create_delegation(
        plan_id,
        DelegationCreateRequest(
            delegator_agent_id=first_id,
            delegate_agent_id=second_id,
            objective="Review analysis",
            input={"score": 82},
        ),
    )

    accepted = await manager.accept_delegation(
        delegation["id"],
        actor_agent_id=second_id,
        note="Accepted",
    )
    assert accepted is not None
    assert accepted["status"] == "accepted"

    completed = await runtime.run_delegation(
        delegation["id"],
        wait=True,
    )
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["result"]["agent_id"] == second_id
    assert completed["result"]["input"] == {"score": 82}

    snapshot = manager.context_snapshot(plan_id)
    assert snapshot is not None
    assert (
        snapshot["values"][
            f"delegations.{delegation['id']}.result"
        ]["agent_id"]
        == second_id
    )

    await runtime.shutdown()
