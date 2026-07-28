from __future__ import annotations

import asyncio
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
    AgentCapabilityCreate,
    AgentCreate,
)
from backend.orchestration.agents import (
    AgentRegistryService,
    AgentRepository,
)
from backend.orchestration.enums import ExecutionPlanStatus
from backend.orchestration.repository import ExecutionPlanRepository
from backend.orchestration.runtime import ExecutionPlanRuntime
from backend.orchestration.runtime_schemas import ExecutionPlanRunRequest
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
async def test_runtime_executes_dag_and_resolves_step_output() -> None:
    scope = make_scope()
    event_bus = EventBus()

    with scope() as session:
        AgentRegistryService(
            AgentRepository(session),
            event_bus,
        ).seed_defaults()
        service = ExecutionPlanService(
            ExecutionPlanRepository(session),
            event_bus,
        )
        plan = await service.create_plan(
            ExecutionPlanCreate(
                title="Runtime plan",
                objective="Collect and write.",
                max_parallel_steps=2,
                steps=[
                    ExecutionPlanStepCreate(
                        step_key="collect",
                        step_type="tool",
                        title="Collect",
                        tool_name="echo",
                        input={"value": "hello"},
                    ),
                    ExecutionPlanStepCreate(
                        step_key="write",
                        step_type="agent",
                        title="Write",
                        agent_role="writer",
                        capability="writing",
                        depends_on=["collect"],
                        input={
                            "source": "$steps.collect.output.value",
                            "label": "Result: {{ steps.collect.output.value }}",
                        },
                    ),
                ],
            )
        )

    runtime = ExecutionPlanRuntime(
        event_bus=event_bus,
        session_factory=scope,
    )
    result = await runtime.start(
        plan["id"],
        ExecutionPlanRunRequest(wait=True),
    )

    assert result is not None
    assert result["status"] == ExecutionPlanStatus.COMPLETED.value
    write = next(
        step for step in result["steps"] if step["step_key"] == "write"
    )
    assert write["output"]["input"]["source"] == "hello"
    assert write["output"]["input"]["label"] == "Result: hello"

    runs = runtime.list_runs(plan["id"])
    assert runs is not None
    assert len(runs) == 2
    assert {run["status"] for run in runs} == {"completed"}


@pytest.mark.asyncio
async def test_runtime_retries_failed_agent_step() -> None:
    scope = make_scope()
    event_bus = EventBus()

    with scope() as session:
        registry = AgentRegistryService(
            AgentRepository(session),
            event_bus,
        )
        await registry.create(
            AgentCreate(
                agent_key="test.flaky",
                display_name="Flaky Agent",
                roles=["flaky-role"],
                executor_ref="test.flaky.executor",
                capabilities=[
                    AgentCapabilityCreate(
                        name="flaky-capability",
                        proficiency=100,
                    )
                ],
            )
        )
        service = ExecutionPlanService(
            ExecutionPlanRepository(session),
            event_bus,
        )
        plan = await service.create_plan(
            ExecutionPlanCreate(
                title="Retry plan",
                objective="Retry once.",
                steps=[
                    ExecutionPlanStepCreate(
                        step_key="flaky",
                        step_type="agent",
                        title="Flaky",
                        agent_role="flaky-role",
                        capability="flaky-capability",
                        max_retries=1,
                    )
                ],
            )
        )

    attempts = 0

    async def flaky_executor(context):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary failure")
        return {"ok": True, "attempt": attempts}

    runtime = ExecutionPlanRuntime(
        event_bus=event_bus,
        session_factory=scope,
    )
    runtime.registry.register_agent(
        "test.flaky.executor",
        flaky_executor,
    )

    result = await runtime.start(
        plan["id"],
        ExecutionPlanRunRequest(wait=True),
    )

    assert result is not None
    assert result["status"] == "completed"
    assert attempts == 2

    runs = runtime.list_runs(plan["id"])
    assert runs is not None
    assert [run["status"] for run in runs] == ["failed", "completed"]
    assert [run["attempt"] for run in runs] == [1, 2]


@pytest.mark.asyncio
async def test_runtime_executes_independent_steps_in_parallel() -> None:
    scope = make_scope()
    event_bus = EventBus()

    with scope() as session:
        service = ExecutionPlanService(
            ExecutionPlanRepository(session),
            event_bus,
        )
        plan = await service.create_plan(
            ExecutionPlanCreate(
                title="Parallel plan",
                objective="Run two probes.",
                max_parallel_steps=2,
                steps=[
                    ExecutionPlanStepCreate(
                        step_key="first",
                        step_type="tool",
                        title="First",
                        tool_name="probe",
                    ),
                    ExecutionPlanStepCreate(
                        step_key="second",
                        step_type="tool",
                        title="Second",
                        tool_name="probe",
                    ),
                ],
            )
        )

    active = 0
    maximum = 0
    release = asyncio.Event()

    async def probe(context):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        if active == 2:
            release.set()
        await asyncio.wait_for(release.wait(), timeout=1)
        active -= 1
        return {"step": context.step_key}

    runtime = ExecutionPlanRuntime(
        event_bus=event_bus,
        session_factory=scope,
    )
    runtime.registry.register_tool("probe", probe)

    result = await runtime.start(
        plan["id"],
        ExecutionPlanRunRequest(wait=True),
    )

    assert result is not None
    assert result["status"] == "completed"
    assert maximum == 2


@pytest.mark.asyncio
async def test_runtime_cancels_active_plan() -> None:
    scope = make_scope()
    event_bus = EventBus()

    with scope() as session:
        service = ExecutionPlanService(
            ExecutionPlanRepository(session),
            event_bus,
        )
        plan = await service.create_plan(
            ExecutionPlanCreate(
                title="Cancellation plan",
                objective="Cancel a long step.",
                steps=[
                    ExecutionPlanStepCreate(
                        step_key="sleep",
                        step_type="tool",
                        title="Sleep",
                        tool_name="sleep",
                        input={"seconds": 30},
                    )
                ],
            )
        )

    runtime = ExecutionPlanRuntime(
        event_bus=event_bus,
        session_factory=scope,
    )
    started = await runtime.start(
        plan["id"],
        ExecutionPlanRunRequest(wait=False),
    )
    assert started is not None

    await asyncio.sleep(0.05)
    cancelled = await runtime.cancel(plan["id"], "Stopped by test.")

    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert cancelled["steps"][0]["status"] == "cancelled"

    runs = runtime.list_runs(plan["id"])
    assert runs is not None
    assert runs[0]["status"] == "cancelled"
