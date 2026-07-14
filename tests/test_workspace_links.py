from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.database.base import Base
from backend.repositories.chats import ChatRepository
from backend.repositories.memory import MemoryRepository
from backend.repositories.projects import ProjectRepository
from backend.repositories.workspaces import WorkspaceRepository


def test_workspace_entity_links() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        workspace = WorkspaceRepository(session).create(
            name="Crypto AI",
            workspace_type="crypto",
        )

        project = ProjectRepository(session).create(
            name="ENA Strategy",
            workspace_id=workspace.id,
        )

        chat = ChatRepository(session).create(
            title="ENA Analysis",
            project_id=project.id,
            workspace_id=workspace.id,
        )

        memory = MemoryRepository(session).create(
            scope="workspace",
            title="Resistance level",
            content="ENA resistance was rejected.",
            source_type="crypto",
            workspace_id=workspace.id,
            project_id=project.id,
        )

        session.commit()

        assert project.workspace_id == workspace.id
        assert chat.workspace_id == workspace.id
        assert memory.workspace_id == workspace.id

        assert len(ProjectRepository(session).list_by_workspace(workspace.id)) == 1
        assert len(ChatRepository(session).list_by_workspace(workspace.id)) == 1
        assert len(
            MemoryRepository(session).list_filtered(
                workspace_id=workspace.id
            )
        ) == 1
