from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.events import EventBus
from backend.database import models as database_models  # noqa: F401
from backend.database.base import Base
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.orchestration.agent_schemas import (
    AgentAssignmentRequest,
    AgentCapabilityCreate,
    AgentCreate,
)
from backend.orchestration.agents import (
    AgentAssignmentService,
    AgentRegistryError,
    AgentRegistryService,
    AgentRepository,
)
from backend.orchestration.repository import ExecutionPlanRepository
from backend.orchestration.schemas import (
    ExecutionPlanCreate,
    ExecutionPlanStepCreate,
)
from backend.orchestration.service import ExecutionPlanService
from backend.task_engine import models as task_models  # noqa: F401


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
async def test_best_agent_is_assigned_to_step() -> None:
    scope = make_scope()

    with scope() as session:
        event_bus = EventBus()
        registry = AgentRegistryService(
            AgentRepository(session),
            event_bus,
        )
        weaker = await registry.create(
            AgentCreate(
                agent_key="analyst.basic",
                display_name="Basic Analyst",
                roles=["analyst"],
                executor_ref="agent.basic",
                priority=50,
                capabilities=[
                    AgentCapabilityCreate(
                        name="analysis",
                        proficiency=65,
                    )
                ],
            )
        )
        stronger = await registry.create(
            AgentCreate(
                agent_key="analyst.expert",
                display_name="Expert Analyst",
                roles=["analyst"],
                executor_ref="agent.expert",
                priority=80,
                capabilities=[
                    AgentCapabilityCreate(
                        name="analysis",
                        proficiency=95,
                    )
                ],
            )
        )

        plans = ExecutionPlanRepository(session)
        plan_service = ExecutionPlanService(plans, event_bus)
        plan = await plan_service.create_plan(
            ExecutionPlanCreate(
                title="Analysis plan",
                objective="Select an analyst.",
                steps=[
                    ExecutionPlanStepCreate(
                        step_key="analyze",
                        step_type="agent",
                        title="Analyze",
                        agent_role="analyst",
                        capability="analysis",
                    )
                ],
            )
        )
        step = plan["steps"][0]

        assignment = AgentAssignmentService(
            AgentRepository(session),
            plans,
            event_bus,
        )
        result = await assignment.assign_plan(
            plan["id"],
            AgentAssignmentRequest(strict=True),
        )

        assert result is not None
        assert result["complete"] is True
        assert result["assigned"][0]["assigned_agent_id"] == stronger["id"]
        assert result["assigned"][0]["assigned_agent_id"] != weaker["id"]

        refreshed = plan_service.get_plan(plan["id"])
        assert refreshed is not None
        assert refreshed["steps"][0]["assigned_agent_id"] == stronger["id"]
        assert refreshed["steps"][0]["assignment"]["agent_key"] == (
            "analyst.expert"
        )


@pytest.mark.asyncio
async def test_strict_assignment_rejects_unmatched_step_without_partial_changes() -> None:
    scope = make_scope()

    with scope() as session:
        event_bus = EventBus()
        registry = AgentRegistryService(
            AgentRepository(session),
            event_bus,
        )
        await registry.create(
            AgentCreate(
                agent_key="writer.only",
                display_name="Writer",
                roles=["writer"],
                executor_ref="writer.executor",
                capabilities=[
                    AgentCapabilityCreate(name="writing", proficiency=90)
                ],
            )
        )

        plans = ExecutionPlanRepository(session)
        plan_service = ExecutionPlanService(plans, event_bus)
        plan = await plan_service.create_plan(
            ExecutionPlanCreate(
                title="Mixed plan",
                objective="Requires two roles.",
                steps=[
                    ExecutionPlanStepCreate(
                        step_key="write",
                        step_type="agent",
                        title="Write",
                        agent_role="writer",
                        capability="writing",
                    ),
                    ExecutionPlanStepCreate(
                        step_key="review",
                        step_type="agent",
                        title="Review",
                        agent_role="reviewer",
                        capability="review",
                    ),
                ],
            )
        )

        assignment = AgentAssignmentService(
            AgentRepository(session),
            plans,
            event_bus,
        )

        with pytest.raises(AgentRegistryError):
            await assignment.assign_plan(
                plan["id"],
                AgentAssignmentRequest(strict=True),
            )

        status = assignment.assignment_status(plan["id"])
        assert status is not None
        assert status["assigned_count"] == 0
