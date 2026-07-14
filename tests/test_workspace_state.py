import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.core.events import EventBus
from backend.database.base import Base
from backend.repositories.chats import ChatRepository
from backend.repositories.memory import MemoryRepository
from backend.repositories.projects import ProjectRepository
from backend.repositories.settings import SettingsRepository
from backend.repositories.workspaces import WorkspaceRepository
from backend.services.workspace_state import ActiveWorkspaceService


def make_service(session: Session) -> ActiveWorkspaceService:
    return ActiveWorkspaceService(
        workspace_repository=WorkspaceRepository(session),
        settings_repository=SettingsRepository(session),
        project_repository=ProjectRepository(session),
        chat_repository=ChatRepository(session),
        memory_repository=MemoryRepository(session),
        event_bus=EventBus(),
    )


@pytest.mark.asyncio
async def test_activate_and_clear_workspace() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        workspace = WorkspaceRepository(session).create(
            name="Crypto AI",
            workspace_type="crypto",
        )

        ProjectRepository(session).create(
            name="ENA Strategy",
            workspace_id=workspace.id,
        )

        service = make_service(session)

        activated = await service.activate_workspace(workspace.id)
        session.commit()

        current = service.get_active_workspace()

        assert activated["id"] == workspace.id
        assert activated["counts"]["projects"] == 1
        assert current is not None
        assert current["id"] == workspace.id

        cleared = await service.clear_active_workspace()
        session.commit()

        assert cleared is True
        assert service.get_active_workspace() is None


@pytest.mark.asyncio
async def test_archived_workspace_cannot_be_activated() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        workspace = WorkspaceRepository(session).create(
            name="Archived",
            status="archived",
        )

        with pytest.raises(ValueError):
            await make_service(session).activate_workspace(workspace.id)
