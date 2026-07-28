from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.events import EventBus
from backend.database.base import Base
from backend.database import models  # noqa: F401
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401
from backend.orchestration.critic import ExecutionPlanCriticService
from backend.orchestration.critic_schemas import ExecutionPlanReviewRequest
from backend.orchestration.enums import ExecutionStepType
from backend.orchestration.repository import ExecutionPlanRepository
from backend.orchestration.schemas import (
    ExecutionPlanCreate,
    ExecutionPlanStepCreate,
)
from backend.orchestration.service import ExecutionPlanService


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
async def test_critic_persists_review_and_detects_risky_tool() -> None:
    scope = make_scope()
    event_bus = EventBus()

    with scope() as session:
        service = ExecutionPlanService(
            ExecutionPlanRepository(session),
            event_bus,
        )
        plan = await service.create_plan(
            ExecutionPlanCreate(
                title="Risky plan",
                objective="Send a final message safely.",
                strategy="Analyze and execute.",
                steps=[
                    ExecutionPlanStepCreate(
                        step_key="analyze",
                        step_type=ExecutionStepType.AGENT,
                        title="Analyze",
                        agent_role="analyst",
                        capability="analysis",
                        max_retries=1,
                    ),
                    ExecutionPlanStepCreate(
                        step_key="send",
                        sequence=20,
                        step_type=ExecutionStepType.TOOL,
                        title="Send message",
                        tool_name="email.send",
                        depends_on=["analyze"],
                    ),
                ],
            )
        )
        plan_id = plan["id"]

    critic = ExecutionPlanCriticService(
        event_bus=event_bus,
        session_factory=scope,
    )
    review = await critic.review(
        plan_id,
        ExecutionPlanReviewRequest(
            auto_fix=False,
            require_pass=False,
        ),
    )

    assert review is not None
    assert review["decision"] in {"revise", "reject"}
    assert any(
        issue["code"] == "risky_tool_without_approval"
        for issue in review["issues"]
    )
    assert critic.get_review(review["id"])["plan_id"] == plan_id


@pytest.mark.asyncio
async def test_auto_fix_adds_approval_and_quality_review() -> None:
    scope = make_scope()
    event_bus = EventBus()

    with scope() as session:
        service = ExecutionPlanService(
            ExecutionPlanRepository(session),
            event_bus,
        )
        plan = await service.create_plan(
            ExecutionPlanCreate(
                title="Auto-fix plan",
                objective="Publish a result with user control.",
                strategy="",
                steps=[
                    ExecutionPlanStepCreate(
                        step_key="analyze",
                        step_type=ExecutionStepType.AGENT,
                        title="Analyze",
                        agent_role="analyst",
                        capability="analysis",
                        timeout_seconds=10,
                    ),
                    ExecutionPlanStepCreate(
                        step_key="publish",
                        sequence=20,
                        step_type=ExecutionStepType.TOOL,
                        title="Publish",
                        tool_name="content.publish",
                        depends_on=["analyze"],
                    ),
                    ExecutionPlanStepCreate(
                        step_key="deliver",
                        sequence=30,
                        step_type=ExecutionStepType.AGENT,
                        title="Deliver",
                        agent_role="writer",
                        capability="report_generation",
                        depends_on=["publish"],
                    ),
                ],
            )
        )
        plan_id = plan["id"]

    critic = ExecutionPlanCriticService(
        event_bus=event_bus,
        session_factory=scope,
    )
    result = await critic.review_and_fix(
        plan_id,
        ExecutionPlanReviewRequest(
            threshold=70,
            auto_fix=True,
            max_rounds=3,
            require_pass=False,
            auto_assign_after_fix=False,
        ),
    )

    assert result is not None
    assert result["applied_fixes"]

    with scope() as session:
        full = ExecutionPlanService(
            ExecutionPlanRepository(session),
            event_bus,
        ).get_plan(plan_id)
        assert full is not None
        assert any(
            step["step_type"] == "approval"
            for step in full["steps"]
        )
        assert any(
            step.get("capability") == "quality_review"
            for step in full["steps"]
        )
        assert full["validation"]["valid"] is True
