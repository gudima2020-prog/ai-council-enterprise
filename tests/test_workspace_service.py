import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.core.events import EventBus
from backend.database.base import Base
from backend.repositories.workspaces import WorkspaceRepository
from backend.services.workspaces import WorkspaceService


def create_test_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


@pytest.mark.asyncio
async def test_workspace_crud() -> None:
    with create_test_session() as session:
        service = WorkspaceService(
            repository=WorkspaceRepository(session),
            event_bus=EventBus(),
        )

        created = await service.create_workspace(
            name="Crypto AI",
            description="Торговая аналитика",
            workspace_type="crypto",
            icon="chart",
            color="#00AA88",
        )
        session.commit()

        loaded = service.get_workspace(created["id"])
        updated = await service.update_workspace(
            workspace_id=created["id"],
            status="archived",
        )
        session.commit()

        deleted = await service.delete_workspace(created["id"])
        session.commit()

        assert loaded is not None
        assert loaded["workspace_type"] == "crypto"
        assert updated is not None
        assert updated["status"] == "archived"
        assert deleted is True


@pytest.mark.asyncio
async def test_rejects_duplicate_name() -> None:
    with create_test_session() as session:
        service = WorkspaceService(
            repository=WorkspaceRepository(session),
            event_bus=EventBus(),
        )

        await service.create_workspace(name="Council")
        session.commit()

        with pytest.raises(ValueError):
            await service.create_workspace(name="Council")


def test_rejects_invalid_type() -> None:
    with create_test_session() as session:
        service = WorkspaceService(
            repository=WorkspaceRepository(session),
            event_bus=EventBus(),
        )

        with pytest.raises(ValueError):
            service._validate_type("invalid")
