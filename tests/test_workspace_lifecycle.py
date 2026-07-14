import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.core.events import EventBus
from backend.database.base import Base
from backend.repositories.workspaces import WorkspaceRepository
from backend.services.workspaces import WorkspaceService


def make_service(session: Session) -> WorkspaceService:
    return WorkspaceService(
        repository=WorkspaceRepository(session),
        event_bus=EventBus(),
    )


@pytest.mark.asyncio
async def test_archive_and_restore_workspace() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        workspace = WorkspaceRepository(session).create(
            name="Crypto AI",
            status="active",
        )

        service = make_service(session)

        archived = await service.change_lifecycle(
            workspace_id=workspace.id,
            action="archive",
        )
        session.commit()

        assert archived is not None
        assert archived["status"] == "archived"

        restored = await service.change_lifecycle(
            workspace_id=workspace.id,
            action="restore",
        )
        session.commit()

        assert restored is not None
        assert restored["status"] == "active"


@pytest.mark.asyncio
async def test_disable_and_enable_workspace() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        workspace = WorkspaceRepository(session).create(
            name="Council",
            status="active",
        )

        service = make_service(session)

        disabled = await service.change_lifecycle(
            workspace_id=workspace.id,
            action="disable",
        )
        session.commit()

        assert disabled is not None
        assert disabled["status"] == "disabled"

        enabled = await service.change_lifecycle(
            workspace_id=workspace.id,
            action="enable",
        )
        session.commit()

        assert enabled is not None
        assert enabled["status"] == "active"


@pytest.mark.asyncio
async def test_rejects_invalid_transition() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        workspace = WorkspaceRepository(session).create(
            name="Research",
            status="archived",
        )

        service = make_service(session)

        with pytest.raises(ValueError):
            await service.change_lifecycle(
                workspace_id=workspace.id,
                action="disable",
            )
