from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database.base import Base
from backend.database import models as database_models  # noqa: F401
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401
from backend.secrets import models as secret_models  # noqa: F401
from backend.orchestration.tool_runtime import ToolExecutionContext, ToolExecutionRuntime
from backend.orchestration.tool_schemas import DirectToolExecutionRequest, ToolCreate
from backend.orchestration.tools import ToolRegistryService, ToolRepository
from backend.secrets.schemas import SecretCreate
from backend.secrets.service import SecretManagerService


class FakeEventBus:
    async def publish(self, *args, **kwargs) -> None:
        return None


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
async def test_tool_secret_binding_is_ephemeral_and_output_is_redacted(monkeypatch) -> None:
    scope = make_scope()
    event_bus = FakeEventBus()
    manager = SecretManagerService(event_bus=event_bus, session_factory=scope)
    manager.seed_builtin_providers()
    manager.seed_access_defaults()
    monkeypatch.setenv("TOOL_TEST_API_KEY", "tool-secret-value")
    secret = await manager.create_secret(
        SecretCreate(
            provider_key="env",
            secret_key="TOOL_TEST_API_KEY",
            provider_ref="TOOL_TEST_API_KEY",
            display_name="Tool test key",
            created_by="owner",
        )
    )

    with scope() as session:
        registry = ToolRegistryService(ToolRepository(session), event_bus)
        tool = await registry.create(
            ToolCreate(
                tool_key="secret.echo.test",
                display_name="Secret echo test",
                handler_ref="plugin.secret.echo",
                risk_level="low",
                metadata={
                    "secret_bindings": {
                        "api_key": secret["reference"],
                    }
                },
            )
        )

    runtime = ToolExecutionRuntime(
        event_bus=event_bus,
        session_factory=scope,
        secret_manager=manager,
    )

    async def handler(context: ToolExecutionContext):
        value = await context.secret("api_key")
        assert value == "tool-secret-value"
        assert value not in repr(context.input)
        return {"debug": f"accidental leak: {value}"}

    runtime.handlers.register("plugin.secret.echo", handler)
    result = await runtime.execute_direct(
        tool["id"],
        DirectToolExecutionRequest(input={"safe": True}),
    )
    assert result is not None
    assert result["output"] == {"debug": "accidental leak: [REDACTED_SECRET]"}

    with scope() as session:
        invocations = ToolRegistryService(
            ToolRepository(session), event_bus
        ).list_invocations()
        assert "tool-secret-value" not in repr(invocations[0])
