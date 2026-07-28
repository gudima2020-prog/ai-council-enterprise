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
    AgentCapabilityCreate,
    AgentCreate,
    AgentMatchRequest,
    AgentUpdate,
)
from backend.orchestration.agents import (
    AgentRegistryService,
    AgentRepository,
)
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
async def test_agent_crud_and_capability_matching() -> None:
    scope = make_scope()

    with scope() as session:
        service = AgentRegistryService(
            AgentRepository(session),
            EventBus(),
        )
        created = await service.create(
            AgentCreate(
                agent_key="custom.analyst",
                display_name="Custom Analyst",
                roles=["analyst"],
                tools=["web_search"],
                executor_ref="custom.executor",
                capabilities=[
                    AgentCapabilityCreate(
                        name="analysis",
                        proficiency=92,
                    )
                ],
            )
        )

        assert created["agent_key"] == "custom.analyst"
        assert created["capabilities"][0]["name"] == "analysis"

        matches = service.match(
            AgentMatchRequest(
                role="analyst",
                capability="analysis",
                required_tools=["web_search"],
            )
        )
        assert matches[0]["agent_id"] == created["id"]

        updated = await service.update(
            created["id"],
            AgentUpdate(priority=90, max_concurrency=3),
        )
        assert updated is not None
        assert updated["priority"] == 90
        assert updated["max_concurrency"] == 3


def test_seed_defaults_is_idempotent() -> None:
    scope = make_scope()

    with scope() as session:
        service = AgentRegistryService(
            AgentRepository(session),
            EventBus(),
        )
        first = service.seed_defaults()
        second = service.seed_defaults()

        assert first == 4
        assert second == 0
        assert len(service.list(limit=100)) == 4
