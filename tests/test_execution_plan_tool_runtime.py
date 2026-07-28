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
from backend.orchestration.repository import ExecutionPlanRepository
from backend.orchestration.schemas import (
    ExecutionPlanCreate,
    ExecutionPlanStepCreate,
)
from backend.orchestration.runtime_schemas import ExecutionPlanRunRequest
from backend.orchestration.tool_runtime import (
    ToolAwareExecutionPlanRuntime,
    ToolExecutionRuntime,
)
from backend.orchestration.tools import ToolRegistryService, ToolRepository


def make_scope():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
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
async def test_tool_step_uses_persistent_registry_and_records_invocation() -> None:
    scope = make_scope()
    event_bus = EventBus()

    with scope() as session:
        ToolRegistryService(ToolRepository(session), event_bus).seed_defaults()
        plan = ExecutionPlanRepository(session).create(
            ExecutionPlanCreate(
                title="Tool plan",
                objective="Execute registered echo tool.",
                steps=[
                    ExecutionPlanStepCreate(
                        step_key="echo",
                        step_type="tool",
                        title="Echo",
                        tool_name="echo",
                        input={"message": "hello"},
                    )
                ],
            )
        )
        plan_id = plan.id

    tool_runtime = ToolExecutionRuntime(
        event_bus=event_bus,
        session_factory=scope,
    )
    runtime = ToolAwareExecutionPlanRuntime(
        event_bus=event_bus,
        session_factory=scope,
        tool_runtime=tool_runtime,
    )

    status = await runtime.start(
        plan_id,
        ExecutionPlanRunRequest(wait=True),
    )

    assert status is not None
    assert status["status"] == "completed"

    with scope() as session:
        invocations = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        ).list_invocations(plan_id=plan_id)
        assert len(invocations) == 1
        assert invocations[0]["tool_key"] == "echo"
        assert invocations[0]["status"] == "completed"
