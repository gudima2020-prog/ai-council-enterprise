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
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401
from backend.orchestration.tool_runtime import (
    ToolExecutionContext,
    ToolExecutionError,
    ToolExecutionRuntime,
    ToolSchemaValidationError,
)
from backend.orchestration.tool_schemas import (
    DirectToolExecutionRequest,
    ToolCreate,
    ToolPermissionCreate,
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
async def test_builtin_tool_executes_and_invocation_is_persisted() -> None:
    scope = make_scope()
    event_bus = EventBus()

    with scope() as session:
        service = ToolRegistryService(ToolRepository(session), event_bus)
        service.seed_defaults()
        echo = next(item for item in service.list() if item["tool_key"] == "echo")

    runtime = ToolExecutionRuntime(
        event_bus=event_bus,
        session_factory=scope,
    )
    result = await runtime.execute_direct(
        echo["id"],
        DirectToolExecutionRequest(input={"value": 42}),
    )

    assert result is not None
    assert result["output"] == {"value": 42}

    with scope() as session:
        invocations = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        ).list_invocations()
        assert len(invocations) == 1
        assert invocations[0]["status"] == "completed"
        assert invocations[0]["output"] == {"value": 42}


@pytest.mark.asyncio
async def test_schema_boundary_rejects_invalid_input() -> None:
    scope = make_scope()
    event_bus = EventBus()

    with scope() as session:
        service = ToolRegistryService(ToolRepository(session), event_bus)
        service.seed_defaults()
        merge = next(item for item in service.list() if item["tool_key"] == "merge")

    runtime = ToolExecutionRuntime(
        event_bus=event_bus,
        session_factory=scope,
    )

    with pytest.raises(ToolSchemaValidationError):
        await runtime.execute_direct(
            merge["id"],
            DirectToolExecutionRequest(input={"items": "not-a-list"}),
        )

    with scope() as session:
        invocations = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        ).list_invocations()
        assert invocations[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_tool_timeout_is_enforced() -> None:
    scope = make_scope()
    event_bus = EventBus()

    with scope() as session:
        service = ToolRegistryService(ToolRepository(session), event_bus)
        created = await service.create(
            ToolCreate(
                tool_key="slow.test",
                display_name="Slow test",
                handler_ref="plugin.slow.test",
                timeout_seconds=1,
                risk_level="low",
            )
        )

    runtime = ToolExecutionRuntime(
        event_bus=event_bus,
        session_factory=scope,
    )

    async def slow_handler(context: ToolExecutionContext):
        await asyncio.sleep(2)
        return {"ok": True}

    runtime.handlers.register("plugin.slow.test", slow_handler)

    with pytest.raises(ToolExecutionError, match="timeout"):
        await runtime.execute_direct(
            created["id"],
            DirectToolExecutionRequest(input={}),
        )

    with scope() as session:
        invocation = ToolRegistryService(
            ToolRepository(session),
            event_bus,
        ).list_invocations()[0]
        assert invocation["status"] == "timed_out"
