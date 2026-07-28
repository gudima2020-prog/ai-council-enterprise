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
from backend.orchestration.tool_schemas import (
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


def test_default_tools_are_seeded_idempotently() -> None:
    scope = make_scope()

    with scope() as session:
        service = ToolRegistryService(ToolRepository(session), EventBus())
        assert service.seed_defaults() == 4
        assert service.seed_defaults() == 0
        assert {item["tool_key"] for item in service.list()} == {
            "echo",
            "merge",
            "select",
            "sleep",
        }


@pytest.mark.asyncio
async def test_high_risk_tool_requires_explicit_allow_and_deny_wins() -> None:
    scope = make_scope()
    event_bus = EventBus()

    with scope() as session:
        service = ToolRegistryService(ToolRepository(session), event_bus)
        tool = await service.create(
            ToolCreate(
                tool_key="dangerous.demo",
                display_name="Dangerous demo",
                handler_ref="plugin.dangerous.demo",
                risk_level="high",
            )
        )
        tool_id = tool["id"]

    with scope() as session:
        service = ToolRegistryService(ToolRepository(session), event_bus)
        row = ToolRepository(session).get(tool_id)
        assert row is not None
        assert service.evaluate(
            tool=row,
            workspace_id=None,
            agent_id=None,
        )["allowed"] is False

        await service.add_permission(
            tool_id,
            ToolPermissionCreate(
                effect="allow",
                created_by="owner",
                reason="Approved for controlled test.",
            ),
        )
        assert service.evaluate(
            tool=row,
            workspace_id=None,
            agent_id=None,
        )["allowed"] is True

        await service.add_permission(
            tool_id,
            ToolPermissionCreate(
                effect="deny",
                created_by="security",
                reason="Emergency block.",
            ),
        )
        decision = service.evaluate(
            tool=row,
            workspace_id=None,
            agent_id=None,
        )
        assert decision["allowed"] is False
        assert "explicit_deny" in decision["reasons"]
